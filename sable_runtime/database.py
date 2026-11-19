"""Pooled PostgreSQL transactions for the campaign ledger."""
from functools import lru_cache


@lru_cache(maxsize=4)
def engine_for(url):
    from sqlalchemy import create_engine
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10,
                         pool_timeout=10, connect_args={'connect_timeout': 10}, hide_parameters=True)


class PostgresConnection:
    """Expose parameterized ledger statements and per-campaign write intent."""
    def __init__(self, connection):
        self.connection = connection
        self.write = False
        self.cursors = []
        self.execute('SET LOCAL search_path TO sable_campaigns, public')
        self.execute("SET LOCAL lock_timeout TO '30s'")

    def execute(self, statement, parameters=()):
        from psycopg2.extras import DictCursor
        cursor = self.connection.cursor(cursor_factory=DictCursor)
        self.cursors.append(cursor)
        # Statements are application-owned SQL; values are always driver parameters.
        cursor.execute(statement.replace('?', '%s'), tuple(parameters))
        return cursor

    def begin_write(self):
        # owned() locks the campaign row before any state/budget/job mutation.
        self.write = True

    def close(self):
        for cursor in self.cursors:
            cursor.close()
