"""Expiring execution slots shared by campaign worker processes."""
import os
import time
from contextlib import contextmanager
from uuid import uuid4


def limit(name, default, maximum=64):
    value = int(os.getenv(name, str(default)))
    if not 1 <= value <= maximum:
        raise ValueError(f'{name} must be between 1 and {maximum}')
    return value


class ExecutionSlots:
    def __init__(self, ledger):
        self.ledger = ledger
        if not ledger.postgres:
            with ledger.connect() as db:
                db.execute('''CREATE TABLE IF NOT EXISTS execution_slots (
                    resource TEXT NOT NULL, slot INTEGER NOT NULL, token TEXT NOT NULL,
                    expires REAL NOT NULL, PRIMARY KEY(resource,slot))''')

    @contextmanager
    def acquire(self, resource, capacity=1):
        token, claimed = uuid4().hex, False
        with self.ledger.connect() as db:
            db.begin_write()
            available = not self.ledger.postgres or db.execute(
                'SELECT pg_try_advisory_xact_lock(hashtextextended(?,0))', (resource,)).fetchone()[0]
            if available:
                now = time.time()
                db.execute('DELETE FROM execution_slots WHERE resource=? AND expires<=?', (resource, now))
                used = {r[0] for r in db.execute('SELECT slot FROM execution_slots WHERE resource=?', (resource,))}
                free = next((slot for slot in range(capacity) if slot not in used), None)
                if free is not None:
                    db.execute('INSERT INTO execution_slots(resource,slot,token,expires) VALUES(?,?,?,?)',
                               (resource, free, token, now+300))
                    claimed = True
        try:
            yield claimed
        finally:
            if claimed:
                with self.ledger.connect() as db:
                    db.execute('DELETE FROM execution_slots WHERE resource=? AND token=?', (resource, token))
