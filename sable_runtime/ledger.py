"""Transactional campaign ownership, action budgets, and durable events."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


class LedgerError(RuntimeError):
    pass


class SQLiteConnection(sqlite3.Connection):
    """Local transaction support for isolated tests."""
    write = False

    def begin_write(self):
        self.execute('BEGIN IMMEDIATE')
        self.write = True


class Ledger:
    def __init__(self, path):
        self.postgres = str(path).startswith(('postgresql://', 'postgresql+psycopg2://'))
        self.path = str(path) if self.postgres else str(Path(path))
        self.order_column = 'created_sequence' if self.postgres else 'rowid'
        if self.postgres:
            with self.connect() as db:
                if db.execute("SELECT to_regclass('sable_campaigns.campaigns')").fetchone()[0] is None:
                    raise RuntimeError('Campaign schema is missing; run alembic upgrade head')
            return
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    max_actions INTEGER NOT NULL, max_units INTEGER NOT NULL,
                    used_actions INTEGER NOT NULL DEFAULT 0, used_units INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS actions (
                    campaign TEXT NOT NULL, id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT, PRIMARY KEY(campaign,id));
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
                    campaign TEXT NOT NULL, action TEXT NOT NULL, kind TEXT NOT NULL,
                    timestamp TEXT NOT NULL, payload TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        if self.postgres:
            from .database import PostgresConnection, engine_for
            raw = engine_for(self.path).raw_connection()
            db = None
            try:
                db = PostgresConnection(raw)
                yield db
                raw.commit()
            except BaseException:
                raw.rollback()
                raise
            finally:
                if db is not None:
                    db.close()
                raw.close()
        else:
            db = sqlite3.connect(self.path, timeout=30, factory=SQLiteConnection)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous=FULL')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    @classmethod
    def configured(cls):
        """Use the same PostgreSQL database as authenticated user accounts."""
        import os
        url = os.environ.get('DATABASE_URL', '')
        if not url.startswith(('postgresql://', 'postgresql+psycopg2://')):
            raise ValueError('Configure DATABASE_URL with a PostgreSQL database')
        return cls(url)

    def create_campaign(self, campaign_id, owner, budget):
        with self.connect() as db:
            db.execute('INSERT INTO campaigns(id,owner,max_actions,max_units) VALUES(?,?,?,?)',
                       (campaign_id, owner, budget.max_actions, budget.max_units))

    @staticmethod
    def owned(db, campaign_id, owner):
        lock = ' FOR UPDATE' if db.write and not isinstance(db, SQLiteConnection) else ''
        row = db.execute('SELECT * FROM campaigns WHERE id=? AND owner=?' + lock, (campaign_id, owner)).fetchone()
        if row is None:
            raise PermissionError('Campaign is not available')
        return row

    @staticmethod
    def event(db, campaign, action, kind, payload):
        db.execute('INSERT INTO events(event_id,campaign,action,kind,timestamp,payload) VALUES(?,?,?,?,?,?)',
                   (str(uuid4()), campaign, action, kind, datetime.now(timezone.utc).isoformat(),
                    json.dumps(payload, allow_nan=False)))

    def claim(self, action, owner, fingerprint, units, payload):
        with self.connect() as db:
            db.begin_write()
            campaign = self.owned(db, action.campaign_id, owner)
            previous = db.execute('SELECT * FROM actions WHERE campaign=? AND id=?',
                                  (action.campaign_id, action.action_id)).fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise LedgerError('Action ID conflicts with an earlier request')
                if previous['status'] != 'succeeded':
                    raise LedgerError('Action already attempted; reconciliation is required')
                return json.loads(previous['result'])
            if campaign['used_actions'] >= campaign['max_actions'] or campaign['used_units'] + units > campaign['max_units']:
                raise LedgerError('Campaign budget exhausted')
            db.execute('UPDATE campaigns SET used_actions=used_actions+1,used_units=used_units+? WHERE id=?',
                       (units, action.campaign_id))
            db.execute('INSERT INTO actions(campaign,id,fingerprint,status) VALUES(?,?,?,?)',
                       (action.campaign_id, action.action_id, fingerprint, 'running'))
            self.event(db, action.campaign_id, action.action_id, 'action_started', payload)
            return None

    def finish(self, action, owner, status, result):
        with self.connect() as db:
            db.begin_write()
            self.owned(db, action.campaign_id, owner)
            changed = db.execute('UPDATE actions SET status=?,result=? WHERE campaign=? AND id=? AND status=?',
                                (status, json.dumps(result, allow_nan=False), action.campaign_id, action.action_id, 'running'))
            if changed.rowcount != 1:
                raise LedgerError('Action is not running')
            self.event(db, action.campaign_id, action.action_id, f'action_{status}', result)

    def events(self, campaign_id, owner, after=0, limit=None):
        with self.connect() as db:
            self.owned(db, campaign_id, owner)
            query = 'SELECT * FROM events WHERE campaign=? AND sequence>? ORDER BY sequence'
            params = [campaign_id, after]
            if limit is not None:
                query += ' LIMIT ?'
                params.append(limit)
            rows = db.execute(query, params).fetchall()
            return [{**dict(row), 'schema_version': 1, 'payload': json.loads(row['payload'])} for row in rows]
