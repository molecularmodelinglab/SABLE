"""Owned text artifacts for scientific inputs that should not enter model context."""
from hashlib import sha256
from uuid import uuid4


class ArtifactStore:
    def __init__(self, ledger):
        self.ledger = ledger
        with ledger.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS campaign_artifacts (
                id TEXT PRIMARY KEY, campaign TEXT NOT NULL, name TEXT NOT NULL,
                content TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL)''')

    def put(self, campaign, owner, name, content):
        if not isinstance(content,str) or not isinstance(name,str) or not name.strip() or len(name)>200:
            raise ValueError('Artifact name and text content are required')
        encoded = content.encode()
        if not encoded or len(encoded)>3_000_000:
            raise ValueError('Artifact must contain at most 3 MB of text')
        with self.ledger.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.ledger.owned(db,campaign,owner)
            count,size=db.execute('SELECT count(*),coalesce(sum(size_bytes),0) FROM campaign_artifacts WHERE campaign=?',(campaign,)).fetchone()
            if count>=16 or size+len(encoded)>32_000_000:
                raise ValueError('Campaign artifact allowance exceeded')
            record={'id':'artifact-'+uuid4().hex,'name':name,'size_bytes':len(encoded),'sha256':sha256(encoded).hexdigest()}
            db.execute('INSERT INTO campaign_artifacts VALUES(?,?,?,?,?,?)',
                       (record['id'],campaign,name,content,record['size_bytes'],record['sha256']))
            self.ledger.event(db,campaign,record['id'],'artifact_uploaded',record)
        return record

    def list(self,campaign,owner):
        with self.ledger.connect() as db:
            self.ledger.owned(db,campaign,owner)
            return [dict(r) for r in db.execute('SELECT id,name,size_bytes,sha256 FROM campaign_artifacts WHERE campaign=? ORDER BY rowid',(campaign,))]

    def read(self,campaign,owner,artifact_id):
        with self.ledger.connect() as db:
            self.ledger.owned(db,campaign,owner)
            row=db.execute('SELECT content FROM campaign_artifacts WHERE campaign=? AND id=?',(campaign,artifact_id)).fetchone()
            if row is None:raise PermissionError('Artifact is not available')
            return row['content']
