"""Durable remote job submission, observation, and explicit reconciliation."""
import json
import os
from contextlib import nullcontext
import time
from uuid import uuid4
from hashlib import sha256
from jsonschema import Draft202012Validator


class JobConflict(RuntimeError):
    pass


TERMINAL = {'succeeded', 'partial', 'failed', 'cancelled', 'timed_out'}
ACTIVE = {'queued', 'submitting', 'running', 'cancelling', 'unknown'}


def trace_inputs(value):
    """Keep large coordinate payloads out of trace previews; retain their identity."""
    if isinstance(value, str) and len(value) > 4096:
        data = value.encode()
        return {'size_bytes': len(data), 'sha256': sha256(data).hexdigest()}
    if isinstance(value, dict):
        return {key: trace_inputs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [trace_inputs(item) for item in value]
    return value


class JobManager:
    def __init__(self, ledger, registry, *, services=None, credentials=None, allowed=None, clock=time.time, authorize=None):
        from .artifacts import ArtifactStore
        self.artifacts = ArtifactStore(ledger)
        from .scheduling import ExecutionSlots
        self.slots = ExecutionSlots(ledger)
        self.authorize = authorize
        self.ledger, self.registry = ledger, registry
        self.services, self.credentials = dict(services or {}), credentials
        self.allowed, self.clock = allowed, clock
        if ledger.postgres:
            return
        with ledger.connect() as db:
            db.begin_write()
            db.execute('''CREATE TABLE IF NOT EXISTS remote_jobs (
                id TEXT PRIMARY KEY, campaign TEXT NOT NULL, action TEXT NOT NULL,
                skill_id TEXT NOT NULL, version TEXT NOT NULL, digest TEXT NOT NULL,
                name TEXT NOT NULL, payload TEXT NOT NULL, binding TEXT NOT NULL,
                status TEXT NOT NULL, remote_id TEXT, result TEXT, error TEXT,
                created REAL NOT NULL, updated REAL NOT NULL, deadline REAL NOT NULL,
                next_poll REAL NOT NULL, lease_until REAL NOT NULL DEFAULT 0,
                token TEXT, batch_index INTEGER NOT NULL DEFAULT 0, UNIQUE(campaign,action,batch_index))''')
            if 'diagnostics' not in {r['name'] for r in db.execute('PRAGMA table_info(remote_jobs)')}:
                db.execute('ALTER TABLE remote_jobs ADD COLUMN diagnostics TEXT')
            if 'credential_ref' not in {r['name'] for r in db.execute('PRAGMA table_info(remote_jobs)')}:
                db.execute('ALTER TABLE remote_jobs ADD COLUMN credential_ref TEXT')
            if 'requested_operation' not in {r['name'] for r in db.execute('PRAGMA table_info(remote_jobs)')}:
                db.execute('ALTER TABLE remote_jobs ADD COLUMN requested_operation TEXT')

    def _binding(self, spec):
        service = self.services.get(spec.service)
        if service is None:
            raise JobConflict('Remote service is unavailable')
        return sha256((service.endpoint or 'sdk-managed').encode()).hexdigest()

    def enqueue(self, action, skill, name, payload, owner, *, batch_index=0, connection=None):
        self._authorize(owner, skill.manifest.id)
        spec = skill.manifest.remote_jobs.get(name)
        if spec is None:
            raise JobConflict('Remote job is not declared')
        binding = self._binding(spec)
        # Resolve credentials before enqueueing; never persist their material.
        reference = spec.credential or self.services[spec.service].credential
        if reference and callable(getattr(self.credentials, 'pin', None)):
            reference = self.credentials.pin(owner, reference)
        context = self._context(action.campaign_id, action.action_id, owner, spec, reference)
        from .executor import contains_secret
        if context.secret and contains_secret(payload, context.secret.get_secret_value()):
            raise JobConflict('Credential material is not a valid job input')
        serialized = json.dumps(payload, sort_keys=True, allow_nan=False)
        now = self.clock()
        with (nullcontext(connection) if connection is not None else self.ledger.connect()) as db:
            if connection is None:
                db.begin_write()
            self.ledger.owned(db, action.campaign_id, owner)
            existing = db.execute('SELECT * FROM remote_jobs WHERE campaign=? AND action=? AND batch_index=?',
                                  (action.campaign_id, action.action_id, batch_index)).fetchone()
            if existing:
                if existing['name'] != name or existing['payload'] != serialized:
                    raise JobConflict('A batch position cannot change its recorded job payload')
                return {'kind': 'remote_job', 'job_id': existing['id'], 'status': existing['status']}
            job_id = 'job-' + uuid4().hex
            db.execute('''INSERT INTO remote_jobs
                (id,campaign,action,skill_id,version,digest,name,payload,binding,status,created,updated,deadline,next_poll,credential_ref,batch_index)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (job_id, action.campaign_id, action.action_id, skill.manifest.id, skill.manifest.version,
                 skill.digest, name, serialized, binding, 'queued', now, now, now + spec.timeout_seconds, now, reference, batch_index))
            self.ledger.event(db, action.campaign_id, action.action_id, 'job_queued',
                              {'job_id': job_id, 'name': name, 'inputs': trace_inputs(payload), 'service': spec.service,
                               'skill_id': skill.manifest.id, 'skill_version': skill.manifest.version,
                               'skill_digest': skill.digest, 'binding_digest': binding})
        return {'kind': 'remote_job', 'job_id': job_id, 'status': 'queued'}

    def enqueue_many(self, action, skill, name, payloads, owner):
        """Atomically queue a bounded batch; each submission has its own lease."""
        if not isinstance(payloads, list) or not 1 <= len(payloads) <= 100:
            raise JobConflict('A remote batch requires one to 100 items')
        with self.ledger.connect() as db:
            db.begin_write()
            self.ledger.owned(db, action.campaign_id, owner)
            receipts = [self.enqueue(action, skill, name, payload, owner,
                                     batch_index=index, connection=db)
                        for index, payload in enumerate(payloads)]
        return {'kind': 'remote_batch', 'jobs': receipts}

    def _authorize(self, owner, skill_id):
        if self.authorize:
            try:
                self.authorize(owner, skill_id)
            except PermissionError as exc:
                raise JobConflict(str(exc)) from None

    def _context(self, campaign, action, owner, spec, reference=None, *, pinned=False):
        from .executor import ExecutionContext
        from pydantic import SecretStr
        service = self.services.get(spec.service)
        if service is None:
            raise JobConflict('Remote service is unavailable')
        configured = spec.credential or getattr(service, 'credential', None)
        if pinned and reference is None and callable(getattr(self.credentials, 'requires_pin', None)) and self.credentials.requires_pin(configured):
            raise JobConflict('Job has no pinned saved credential; review its original provider account')
        reference = reference or configured
        try:
            secret = self.credentials(owner, reference) if reference and self.credentials else None
        except Exception:
            raise JobConflict('Remote credential is unavailable; check the saved account credential') from None
        if reference and (not isinstance(secret, SecretStr) or not secret.get_secret_value()):
            raise JobConflict('Remote credential is unavailable')
        artifacts = self.artifacts
        def write_artifact(name, content):
            from hashlib import sha256
            digest = sha256(content.encode()).hexdigest()
            for existing in artifacts.list(campaign, owner):
                if existing['name'] == name and existing['sha256'] == digest:
                    return existing
            return artifacts.put(campaign, owner, name, content)
        return ExecutionContext(campaign, action, service, secret, write_artifact=write_artifact)

    def _read(self, db, campaign, job_id, owner):
        self.ledger.owned(db, campaign, owner)
        row = db.execute('SELECT * FROM remote_jobs WHERE campaign=? AND id=?', (campaign, job_id)).fetchone()
        if row is None:
            raise PermissionError('Job is not available')
        return dict(row)

    def public(self, row):
        fields = ('id', 'campaign', 'action', 'skill_id', 'version', 'status', 'remote_id',
                  'error', 'created', 'updated', 'deadline', 'next_poll')
        local_cancel = row['status'] == 'queued' or (row['status'] == 'timed_out' and not row['remote_id'])
        try:
            spec = self.registry.get(row['skill_id'], row['version']).manifest.remote_jobs[row['name']]
            can_cancel, can_reconcile = local_cancel or bool(spec.cancel), spec.reconcilable
        except (ValueError, KeyError):
            can_cancel, can_reconcile = local_cancel, False
        return {**{k: row[k] for k in fields}, 'result': json.loads(row['result']) if row['result'] else None,
                'diagnostics': json.loads(row['diagnostics']) if row['diagnostics'] else None,
                'can_cancel': can_cancel, 'can_reconcile': can_reconcile}

    def list(self, campaign, owner):
        with self.ledger.connect() as db:
            self.ledger.owned(db, campaign, owner)
            return [self.public(r) for r in db.execute('SELECT * FROM remote_jobs WHERE campaign=? ORDER BY created', (campaign,))]

    def _configuration(self, row):
        key = (row['skill_id'], row['version'])
        if self.allowed is not None and key not in self.allowed:
            raise JobConflict('Job skill is no longer authorized')
        try:
            skill = self.registry.get(*key)
        except ValueError:
            raise JobConflict('Pinned job skill is unavailable') from None
        if skill.digest != row['digest']:
            raise JobConflict('Pinned job skill has changed')
        spec = skill.manifest.remote_jobs[row['name']]
        if self._binding(spec) != row['binding']:
            raise JobConflict('Job service endpoint has changed')
        return skill, spec

    def request(self, campaign, job_id, owner, operation):
        """Persist a user control request without contacting the provider."""
        if operation not in {'poll', 'cancel'}:
            raise ValueError('Unsupported job control')
        with self.ledger.connect() as db:
            db.begin_write()
            row = self._read(db, campaign, job_id, owner)
            if row['status'] in {'succeeded','partial','failed','cancelled'}:
                return self.public(row)
            if row['status'] == 'unknown':
                raise JobConflict('Submission requires reconciliation')
            if operation == 'cancel' and not self.public(row)['can_cancel']:
                raise JobConflict('The service does not support cancellation through this skill')
            # A poll must never overwrite a previously requested cancellation.
            requested = 'cancel' if row['requested_operation'] == 'cancel' else operation
            if row['requested_operation'] == requested:
                return self.public(row)
            db.execute('UPDATE remote_jobs SET requested_operation=?,next_poll=? WHERE id=?', (requested,self.clock(),job_id))
            self.ledger.event(db,campaign,row['action'],'job_control_requested',{'job_id':job_id,'operation':requested})
        return self.public(row)

    def _submission_available(self, db, row, owner):
        from .scheduling import limit
        configured = json.loads(os.getenv('SABLE_PROVIDER_JOB_LIMITS') or '{"boltz":1,"boltz-platform":4}')
        capacity = configured.get(row['skill_id'], 4)
        if type(capacity) is not int or not 1 <= capacity <= 1000:
            raise ValueError('Provider job limits must be integers between 1 and 1000')
        for resource in ('provider:'+row['skill_id']+':'+row['binding'], 'jobs-owner:'+str(owner)):
            if self.ledger.postgres and not db.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(?,0))',(resource,)).fetchone()[0]:
                return False
        active = "(status IN ('submitting','running','cancelling','unknown') OR (status='timed_out' AND remote_id IS NOT NULL))"
        provider_count = db.execute(f'SELECT count(*) FROM remote_jobs WHERE skill_id=? AND binding=? AND {active}', (row['skill_id'],row['binding'])).fetchone()[0]
        user_count = db.execute(f'SELECT count(*) FROM remote_jobs j JOIN campaigns c ON c.id=j.campaign WHERE c.owner=? AND {active}',(owner,)).fetchone()[0]
        return provider_count < capacity and user_count < limit('SABLE_MAX_USER_JOBS',4,1000)

    def drive(self, campaign, job_id, owner, *, cancel=False):
        """Perform at most one remote request phase under a fenced local lease."""
        now = self.clock()
        with self.ledger.connect() as db:
            db.begin_write()
            row = self._read(db, campaign, job_id, owner)
            cancel = cancel or row['requested_operation'] == 'cancel'
            was_cancelling = row['status'] == 'cancelling'
            if row['lease_until'] > now:
                raise JobConflict('Job operation is already in progress')
            if cancel and (row['status'] == 'queued' or (row['status'] == 'timed_out' and not row['remote_id'])):
                self._update(db, row, 'cancelled')
                return {**self.public(row), 'status': 'cancelled'}
            if row['status'] == 'timed_out' and not row['remote_id']:
                return self.public(row)
            if row['status'] == 'submitting':
                self._update(db, row, 'unknown', error='submission_outcome_unknown')
                return {**self.public(row), 'status': 'unknown'}
            if row['status'] == 'cancelling':
                row['status'] = 'running'
            if row['status'] in {'succeeded', 'partial', 'failed', 'cancelled'}:
                return self.public(row)
            skill, spec = self._configuration(row)
            self._authorize(owner, row['skill_id'])
            if row['status'] == 'unknown':
                raise JobConflict('Submission requires explicit reconciliation; it will not be retried')
            if cancel and not spec.cancel:
                raise JobConflict('The service does not support cancellation through this skill')
            if not cancel and row['status'] != 'timed_out' and now >= row['deadline']:
                self._update(db, row, 'timed_out', error='local_wait_deadline_exceeded')
                return {**self.public(row), 'status': 'timed_out'}
            if not cancel and now < row['next_poll']:
                return self.public(row)
            action = db.execute('SELECT status FROM actions WHERE campaign=? AND id=?', (campaign, row['action'])).fetchone()
            if action is None or action['status'] != 'succeeded':
                raise JobConflict('Enqueue action has not committed successfully')
            phase = 'cancel' if cancel else ('submit' if row['status'] == 'queued' else 'poll')
            if phase == 'submit' and not self._submission_available(db, row, owner):
                db.execute('UPDATE remote_jobs SET next_poll=?,error=? WHERE id=?', (now+10, 'waiting_for_capacity', job_id))
                if row['error'] != 'waiting_for_capacity':
                    self.ledger.event(db,campaign,row['action'],'job_deferred',{'job_id':job_id,'reason':'waiting_for_capacity'})
                return self.public(row)
            db.execute('UPDATE remote_jobs SET requested_operation=NULL WHERE id=?', (job_id,))
            # A missing/revoked credential must not turn an unsent job into an uncertain submission.
            context = self._context(campaign, row['action'], owner, spec, row['credential_ref'], pinned=True)
            token = uuid4().hex
            db.execute('UPDATE remote_jobs SET status=?,token=?,lease_until=?,updated=? WHERE id=?',
                       ('submitting' if phase == 'submit' else 'cancelling' if cancel else row['status'], token, now+300, now, job_id))
            self.ledger.event(db, campaign, row['action'], 'job_' + phase + '_started', {'job_id': job_id})
        try:
            handler = self.registry.remote_handler(skill, spec, phase)
            payload = json.loads(row['payload'])
            output = handler(payload, context) if phase == 'submit' else handler(row['remote_id'], payload, context)
            output = json.loads(json.dumps(output, allow_nan=False))
            from .executor import contains_secret
            if context.secret and contains_secret(output, context.secret.get_secret_value()):
                raise ValueError('Credential appeared in remote response')
            remote_id = output.get('remote_id', row['remote_id'])
            status = output.get('status', 'running' if remote_id else None)
            if status not in {'running', 'succeeded', 'partial', 'failed', 'cancelled'}:
                raise ValueError('Invalid remote status')
            if remote_id is not None and (not isinstance(remote_id, str) or not remote_id or len(remote_id) > 512):
                raise ValueError('Invalid remote identifier')
            if status == 'running' and not remote_id:
                raise ValueError('Missing remote identifier')
            result = output.get('result')
            diagnostics = output.get('diagnostics')
            if diagnostics is not None and not isinstance(diagnostics, dict):
                raise ValueError('Invalid remote diagnostics')
            if status in {'succeeded', 'partial'}:
                Draft202012Validator(spec.output_schema).validate(result)
            elif result is not None:
                raise ValueError('Nonterminal results must not become scientific evidence')
            error = 'remote_job_failed' if status == 'failed' else None
        except Exception:
            status = 'unknown' if phase == 'submit' else row['status']
            if status == 'cancelling':
                status = 'running'
            error = 'submission_outcome_unknown' if phase == 'submit' else 'remote_request_failed'
            remote_id, result = row['remote_id'], None
            diagnostics = None
        if row['status'] == 'timed_out' and status == 'running':
            status = 'timed_out'
        elif status == 'running' and (cancel or was_cancelling):
            status = 'cancelling'
        with self.ledger.connect() as db:
            db.begin_write()
            current = self._read(db, campaign, job_id, owner)
            if current['token'] != token:
                raise JobConflict('Job operation lease was superseded')
            self._update(db, current, status, remote_id=remote_id, result=result, error=error,
                         next_poll=self.clock()+spec.poll_interval_seconds, diagnostics=diagnostics)
        return next(j for j in self.list(campaign, owner) if j['id'] == job_id)

    def _update(self, db, row, status, *, remote_id=None, result=None, error=None, next_poll=None, diagnostics=None):
        db.execute('''UPDATE remote_jobs SET status=?,remote_id=?,result=?,error=?,updated=?,next_poll=?,
                      token=NULL,lease_until=0,diagnostics=? WHERE id=?''',
                   (status, remote_id if remote_id is not None else row['remote_id'],
                    json.dumps(result, allow_nan=False) if result is not None else row['result'], error,
                    self.clock(), next_poll if next_poll is not None else row['next_poll'],
                    json.dumps(diagnostics, allow_nan=False) if diagnostics is not None else None, row['id']))
        self.ledger.event(db, row['campaign'], row['action'], 'job_' + status,
                          {'job_id': row['id'], 'remote_id': remote_id if remote_id is not None else row['remote_id'],
                           'status': status, 'error': error, 'result': result, 'diagnostics': diagnostics})

    def reconcile(self, campaign, job_id, owner, remote_id):
        if not isinstance(remote_id, str) or not remote_id.strip() or len(remote_id) > 512:
            raise JobConflict('A valid remote identifier is required')
        with self.ledger.connect() as db:
            db.begin_write()
            row = self._read(db, campaign, job_id, owner)
            _, spec = self._configuration(row)
            self._authorize(owner, row['skill_id'])
            if not spec.reconcilable:
                raise JobConflict('This service has no safe lookup for an uncertain submission; operator review is required')
            context = self._context(campaign, row['action'], owner, spec, row['credential_ref'], pinned=True)
            if context.secret and context.secret.get_secret_value() in remote_id:
                raise JobConflict('Credential material is not a remote identifier')
            if row['lease_until'] > self.clock():
                raise JobConflict('Job operation is still in progress')
            if row['status'] not in {'unknown', 'submitting'}:
                raise JobConflict('Only uncertain submissions can be reconciled')
            self._update(db, row, 'running', remote_id=remote_id, next_poll=self.clock())
            self.ledger.event(db, campaign, row['action'], 'job_reconciled', {'job_id': job_id, 'remote_id': remote_id})
        return next(j for j in self.list(campaign, owner) if j['id'] == job_id)

    def drive_due(self, limit=32, *, requested_only=False):
        """Advance due jobs without a browser or model request; one phase per job."""
        with self.ledger.connect() as db:
            requested = "AND j.requested_operation IS NOT NULL" if requested_only else ''
            rows = db.execute(f'''SELECT j.id,j.campaign,c.owner,
                row_number() OVER (PARTITION BY c.owner ORDER BY j.next_poll,j.created) AS owner_rank
                FROM remote_jobs j
                JOIN campaigns c ON c.id=j.campaign
                JOIN actions a ON a.campaign=j.campaign AND a.id=j.action
                WHERE (j.status IN ('queued','submitting','running','cancelling') OR (j.status='timed_out' AND j.requested_operation IS NOT NULL)) {requested}
                AND j.next_poll<=? AND j.lease_until<=? AND a.status='succeeded'
                ORDER BY owner_rank,j.next_poll LIMIT ?''', (self.clock(),self.clock(),max(64,limit))).fetchall()
        outcomes = []
        from .scheduling import limit as setting
        for row in rows:
            with self.slots.acquire('owner:'+str(row['owner']), setting('SABLE_MAX_USER_OPERATIONS',1)) as owner_slot:
                if not owner_slot:
                    continue
                with self.slots.acquire('campaign:'+row['campaign']) as campaign_slot:
                    if not campaign_slot:
                        continue
                    outcomes.append(self._drive_due_job(row))
            if len(outcomes) >= limit:
                break
        return outcomes

    def _drive_due_job(self, row):
        try:
            result = self.drive(row['campaign'],row['id'],row['owner'])
            status = result['status']
        except JobConflict:
            with self.ledger.connect() as db:
                db.begin_write()
                current = self._read(db,row['campaign'],row['id'],row['owner'])
                if current['lease_until'] <= self.clock():
                    db.execute('UPDATE remote_jobs SET next_poll=?,error=? WHERE id=?',
                               (self.clock()+60,'configuration_or_access_unavailable',row['id']))
                    self.ledger.event(db,row['campaign'],current['action'],'job_deferred',{'job_id':row['id'],'reason':'configuration_or_access_unavailable'})
            status = 'configuration_or_concurrency_conflict'
        return {'job_id':row['id'],'campaign':row['campaign'],'owner':str(row['owner']),'status':status}
