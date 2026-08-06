# v1.4 build verification

Performed in the artifact-generation environment:

```text
Python compileall: passed
Unit tests: 111 passed
Alembic offline SQL: 0001 through 0007 generated successfully
SQLAlchemy metadata: 34 tables
```

Not performed here:

```text
Docker Compose startup
PostgreSQL online migration
OKX Demo authenticated reconciliation
Actual execute-soak order submission
```

Those checks require the operator's local Docker services and private OKX Demo
credentials. They must not be treated as passed until run locally.
