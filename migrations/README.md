# Database Migrations

This directory contains Alembic database migrations for LIZARD.

## Creating a New Migration

### Auto-generate from model changes
```bash
alembic revision --autogenerate -m "description of changes"
```

### Create empty migration
```bash
alembic revision -m "description of changes"
```

## Running Migrations

### Upgrade to latest
```bash
alembic upgrade head
```

### Upgrade by one revision
```bash
alembic upgrade +1
```

### Downgrade by one revision
```bash
alembic downgrade -1
```

### Show current revision
```bash
alembic current
```

### Show migration history
```bash
alembic history
```

## Docker Usage

Migrations are automatically run when starting with docker-compose:
```bash
docker-compose up migrations
```

## Initial Setup

To create the initial migration:
```bash
# Make sure DATABASE_URL is set in .env
alembic revision --autogenerate -m "initial schema"

# Review the generated migration in migrations/versions/
# Then apply it:
alembic upgrade head
```

## Best Practices

1. Always review auto-generated migrations before applying
2. Test migrations on a development database first
3. Backup production data before running migrations
4. Never edit applied migrations - create new ones instead
5. Keep migrations small and focused
6. Write meaningful commit messages
