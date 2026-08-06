# v1.5 artifact verification

Performed in the artifact-generation environment:

```text
Python compileall: passed
Unit tests: 121 passed
Alembic offline SQL: 0001 through 0008 generated successfully
SQLAlchemy metadata: 37 tables
```

Not performed in the artifact-generation environment:

```text
Docker Compose startup
PostgreSQL online migration
Authenticated OKX Demo reconciliation
Multi-day Demo data collection
Operator strategy-control write against the user's database
```

Those checks require the operator's local Docker services, PostgreSQL volume,
and private OKX Demo credentials. They must not be treated as passed until run
locally.
