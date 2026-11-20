"""Separate human operator privileges; no order, intent or financial writer.

These statements are installation DATA. Importing this module never grants a
role, prepares an account, clears a stop or arms a process.
"""

import re

from app.domain import native_live_financial_epoch_schema_v1 as financial
from app.domain import native_live_ledger_schema_v1 as ledger
from app.domain.source_primitives import canonical, sha

SCHEMA = "ctcc.native_live.operator_control.v1"
READ_TABLES = tuple(
    dict.fromkeys(
        (
            *ledger.TABLES,
            *ledger.METADATA_READ_TABLES,
            *ledger.LEGACY_READ_TABLES,
            *financial.TABLES,
        )
    )
)
WRITE_TABLE = ledger.TABLES[0]
EFFECTIVE_TABLE_PRIVILEGES = frozenset(
    {(name, "SELECT") for name in READ_TABLES}
    | {(WRITE_TABLE, "INSERT"), (WRITE_TABLE, "UPDATE")}
)
SPEC_SHA256 = sha(
    canonical(
        {
            "schema": SCHEMA,
            "head": ledger.HEAD,
            "table_privileges": sorted(EFFECTIVE_TABLE_PRIVILEGES),
            "positive_functions": [],
            "order_authority": False,
        }
    )
)


def live_operator_control_grant_sql_v1(role: str) -> tuple[str, ...]:
    if (
        type(role) is not str
        or role == "public"
        or role.startswith("pg_")
        or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role) is None
    ):
        raise ValueError("native_live_operator_identifier_refused")
    return (
        f'GRANT USAGE ON SCHEMA public TO "{role}"',
        *(f'GRANT SELECT ON TABLE public.{table} TO "{role}"' for table in READ_TABLES),
        f'GRANT INSERT ON TABLE public.{WRITE_TABLE} TO "{role}"',
        f'GRANT UPDATE ON TABLE public.{WRITE_TABLE} TO "{role}"',
    )
