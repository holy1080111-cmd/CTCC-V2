"""Source-only DDL registry for the pending truthful Paper58 observer.

No migration is executed against a database. A revision that asks for a bound
connection is unresolved in its entirety; no result, role or row is invented.
The output cannot establish installed schema, actor rights or startup readiness.
"""

from __future__ import annotations

import hashlib
import io
import re

from scripts import paper58_source_coverage_v1 as coverage

SCHEMA = "ctcc.paper58.source_effective_ddl.v1"
_DOLLAR = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", re.ASCII)
_FUNCTION = re.compile(
    r"\ACREATE\s+(?P<replace>OR\s+REPLACE\s+)?FUNCTION\s+"
    r"(?P<name>(?:public\.)?[a-z_][a-z0-9_]*)\s*\(",
    re.IGNORECASE | re.ASCII,
)
_TRIGGER = re.compile(
    r"\ACREATE\s+(?P<constraint>CONSTRAINT\s+)?TRIGGER\s+"
    r"(?P<name>[a-z_][a-z0-9_]*)\b.*?\bON\s+"
    r"(?P<table>(?:public\.)?[a-z_][a-z0-9_]*)\b",
    re.IGNORECASE | re.DOTALL | re.ASCII,
)
_OBJECT_CHANGE = re.compile(
    r"\A(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?|ALTER\s+|DROP\s+)"
    r"(?:FUNCTION|TRIGGER)\b",
    re.IGNORECASE | re.ASCII,
)


class SourceObservationRequired(ValueError):
    """Offline source declarations cannot supply catalog observations."""


def _need(value):
    if value is not True:
        raise ValueError("paper58_source_ddl_unresolved") from None


def _refuse_bind():
    raise SourceObservationRequired("paper58_source_requires_actual_catalog")


def _statements(sql):
    """Split emitted source SQL without splitting quoted function bodies."""
    _need(type(sql) is str and len(sql) <= 16 * 1024 * 1024)
    position, start, quote, block_depth = 0, 0, None, 0
    pieces = []
    while position < len(sql):
        if quote is not None:
            if quote in ("'", '"') and sql.startswith(quote * 2, position):
                position += 2
                continue
            if sql.startswith(quote, position):
                position += len(quote)
                quote = None
                continue
        elif block_depth:
            if sql.startswith("/*", position):
                block_depth += 1
                position += 2
                continue
            if sql.startswith("*/", position):
                block_depth -= 1
                position += 2
                continue
        elif sql.startswith("--", position):
            newline = sql.find("\n", position)
            position = len(sql) if newline < 0 else newline + 1
            continue
        elif sql.startswith("/*", position):
            block_depth = 1
            position += 2
            continue
        elif sql[position] in ("'", '"'):
            quote = sql[position]
        elif sql[position] == "$" and (match := _DOLLAR.match(sql, position)):
            quote = match.group()
            position = match.end()
            continue
        elif sql[position] == ";":
            pieces.append(sql[start : position + 1])
            start = position + 1
        position += 1
    _need(quote is None and block_depth == 0)
    if sql[start:].strip():
        pieces.append(sql[start:])
    # Include every byte, including the final whitespace-only suffix.
    if start < len(sql) and not sql[start:].strip() and pieces:
        pieces[-1] += sql[start:]
    _need("".join(pieces) == sql or sql.strip() == "")
    return tuple(pieces)


def _emit_revision(context, module):
    from alembic.operations import Operations

    # This is Alembic's genuine offline context. Its connection getter is
    # replaced by a refusal, never a fake connection or observation result.
    with Operations.context(context) as operations:
        operations.get_bind = _refuse_bind
        module.upgrade()


def _registry(emissions):
    functions, triggers, unresolved = {}, {}, []
    versions = []
    for emission in emissions:
        if emission["status"] != "SOURCE_SQL_EMITTED":
            unresolved.append(
                {"revision": emission["revision"], "reason": emission["status"]}
            )
            continue
        for ordinal, statement in enumerate(_statements(emission["sql"])):
            text = statement.strip()
            function = _FUNCTION.match(text)
            trigger = _TRIGGER.match(text)
            if function is None and trigger is None:
                if _OBJECT_CHANGE.match(text):
                    unresolved.append(
                        {
                            "revision": emission["revision"],
                            "reason": "object_change_requires_catalog_resolution",
                            "source_sql_sha256": hashlib.sha256(
                                statement.encode()
                            ).hexdigest(),
                        }
                    )
                continue
            kind = "function" if function else "trigger"
            match = function or trigger
            name = match["name"].removeprefix("public.")
            key = (
                name
                if function
                else match["table"].removeprefix("public.") + "." + name
            )
            record = {
                "kind": kind,
                "name": name,
                "table": None if function else match["table"].removeprefix("public."),
                "revision": emission["revision"],
                "statement_ordinal": ordinal,
                "source_sql": statement,
                "source_sql_sha256": hashlib.sha256(statement.encode()).hexdigest(),
            }
            target = functions if function else triggers
            if key in target and not (function and function["replace"]):
                unresolved.append(
                    {
                        "revision": emission["revision"],
                        "reason": "overload_or_drop_history_requires_actual_catalog",
                        "kind": kind,
                        "name": name,
                    }
                )
            target[key] = record
            versions.append(record)
    return functions, triggers, versions, unresolved


def effective_source_ddl_v1():
    # Avoid migrations/env.py: it reads Settings and constructs online URLs.
    # Only genuine model registration and migration source modules are loaded.
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory

    import app.database.models  # noqa: F401 -- canonical naming registry only
    from app.database.base import Base

    source_files = coverage._migration_domain_sources()
    script = ScriptDirectory(str(coverage.ROOT / "migrations"))
    revisions = tuple(reversed(tuple(script.walk_revisions("base", "0058"))))
    _need(
        tuple(item.revision for item in revisions)
        == tuple(f"{i:04d}" for i in range(1, 59))
    )
    emissions = []
    for revision in revisions:
        output = io.StringIO()
        context = MigrationContext.configure(
            dialect_name="postgresql",
            opts={
                "as_sql": True,
                "literal_binds": True,
                "output_buffer": output,
                "target_metadata": Base.metadata,
            },
        )
        try:
            _emit_revision(context, revision.module)
        except SourceObservationRequired:
            # Discard ALL partial SQL from this revision. A preceding CREATE
            # is not proof of the branch that a real catalog read would take.
            emissions.append(
                {
                    "revision": revision.revision,
                    "status": "ACTUAL_CATALOG_REQUIRED",
                    "sql": "",
                }
            )
        else:
            emissions.append(
                {
                    "revision": revision.revision,
                    "status": "SOURCE_SQL_EMITTED",
                    "sql": output.getvalue(),
                }
            )
    functions, triggers, versions, unresolved = _registry(emissions)
    _need(
        {
            name: raw
            for name, (raw, _tree) in coverage._migration_domain_sources().items()
        }
        == {name: raw for name, (raw, _tree) in source_files.items()}
    )
    return {
        "schema": SCHEMA,
        "head": "0058",
        "source_files": {
            name: hashlib.sha256(raw).hexdigest()
            for name, (raw, _tree) in sorted(source_files.items())
        },
        "revision_status": [
            {
                "revision": item["revision"],
                "status": item["status"],
                "source_sql_sha256": hashlib.sha256(item["sql"].encode()).hexdigest(),
            }
            for item in emissions
        ],
        "latest_source_function_definitions": functions,
        "latest_source_trigger_definitions": triggers,
        "latest_declarations_are_not_a_complete_installed_object_set": True,
        "definition_history": versions,
        "unresolved": unresolved,
        "complete_effective_catalog": False,
        "unresolved_prefix_requires_genuine_reference": True,
        "database_connections": 0,
        "observed_query_results_supplied": 0,
        "startup_admitted": False,
        "execution_authority": False,
    }
