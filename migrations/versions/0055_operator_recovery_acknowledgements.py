"""Explicit stopped recovery and one-time policy-counter acknowledgements.

Revision ID: 0055
Revises: 0054

Installs no role grants, clears no stop, initializes no counter, enables no
Arm/order writes, and preserves all historical strict54 source contracts.
New genuine current55 catalog admission is required by new native consumers.
"""

import hashlib
from pathlib import Path

from alembic import op
from sqlalchemy import text

from app.domain import controlled_history_current55_schema_v2 as controlled
from app.domain import demo_operator_ack_schema_v55 as schema
from app.domain import native_close_intent_catalog_v55 as close_index_catalog
from app.domain import native_close_intent_schema_v55 as close_intent
from app.domain import native_closed_report_catalog_v55 as report_catalog
from app.domain import native_closed_report_schema_v55 as closed_report
from app.domain import native_filled_flat_closure_schema_v55 as closure
from app.domain import (
    native_leverage_configuration_catalog_v55 as configuration_catalog,
)
from app.domain import (
    native_leverage_configuration_enforcement_v55 as configuration_enforcement,
)
from app.domain import native_leverage_configuration_schema_v55 as configuration_schema
from app.domain import native_submission_reporting_schema_v55 as submission
from app.domain import native_terminal_catalog_v55 as terminal_catalog
from app.domain import native_zero_fok_closure_schema_v55 as zero_closure

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None
_FROZEN_DDL_SHA256 = "9535c233c7398c9f70d407a6c172576f251cc4d6d2cd9ce2a0e716164b69a629"
_FROZEN_RESTORE_SHA256 = (
    "df3b4f0946a1d4e2a235952558f97740c3186e5078a6449c80f121cab4ce29f2"
)
_PARENTS = (
    "qualification_account_scopes",
    "qualification_reservations",
    "demo_account_controls",
    "demo_control_journal",
    "demo_account_capture_events",
    "public_runtime_raw_bundles_v1",
    "demo_collector_current_revision_lineages",
    "demo_collector_issued_account_revisions",
)
_NEW_TABLES = (
    "demo_control_clear_acknowledgements_v2",
    "demo_policy_counter_initializations_v1",
)
_NEW_FUNCTIONS = (
    "control_clear_ack_guard_v2",
    "control_clear_ack_commit_guard_v2",
    "policy_counter_initialization_guard_v1",
    "policy_counter_initialization_commit_guard_v1",
    "control_operator_ack_immutable_v55",
)
_LOCK = (
    "LOCK TABLE "
    + ",".join("public." + name for name in _PARENTS)
    + " IN ACCESS EXCLUSIVE MODE NOWAIT"
)


_EXTENSION_LOCK = (
    "LOCK TABLE public.demo_account_history_native_members,"
    "public.demo_account_history_partition_checkpoints,"
    "public.demo_account_observation_batches,"
    "public.qualification_reservation_transitions,"
    "public.qualification_submission_outcomes,public.qualification_report_spool,"
    "public.qualification_report_projection_receipts IN ACCESS EXCLUSIVE MODE NOWAIT"
)
_EXTENSION_TABLES = {
    "demo_account_history_native_members",
    "demo_account_history_partition_checkpoints",
    "demo_account_observation_batches",
    "qualification_reservation_transitions",
    "qualification_submission_outcomes",
    "qualification_report_spool",
    "qualification_report_projection_receipts",
}
# Exact PUBLIC source files, independently reviewable before migration use.
# A later mechanical style derivative must explicitly repin these bytes.
_FROZEN_CONTROLLED_SOURCE_SHA256 = (
    "d87ab0accb1762adb0e9b97a87d34ee228c05ea641e29a1ee6dc74d5d9dcdc25"
)
_FROZEN_SUBMISSION_SOURCE_SHA256 = (
    "8ba5eff198408c289995e7fb6d29e48430ba5904b4f26b047b087fdb1b66bfee"
)
_FROZEN_CLOSURE_SOURCE_SHA256 = (
    "5a7698f6a90832865784f2c4860c3c930fa6f8b1ce01ec5b05e197f654545d51"
)
_FROZEN_FILLED_CLOSURE_SOURCE_SHA256 = (
    "0d40873195bced58a688bf150fc78543be87218a20909bf1fc5e075a665a1769"
)
_FROZEN_TERMINAL_CATALOG_SOURCE_SHA256 = (
    "bd977a9735f76b231a67e005a246971314a8ca3ead0a0cdca0cf52ac500247d3"
)
_FROZEN_CLOSED_REPORT_SOURCE_SHA256 = (
    "0932089262dbb5b1942034d66ae141a8808bf18551145fa5b7d6bce3e22a3a48"
)
_FROZEN_REPORT_CATALOG_SOURCE_SHA256 = (
    "7544d567232005bc289d6bff173724574649af2936d3881ddb92795ccd22b70f"
)
_FROZEN_CLOSE_INTENT_SOURCE_SHA256 = (
    "b9fa2c790cd0000cfa1e8f0b07ac54f7bee3e5a648e4d3cb119d5e737ee4cf60"
)
_FROZEN_CLOSE_INDEX_CATALOG_SOURCE_SHA256 = (
    "07cfb1c56fd3bc71238c09a11116fd43770455190bb9ac944a61deabc9ffe6a3"
)


_FROZEN_CONFIGURATION_SCHEMA_SOURCE_SHA256 = (
    "df7f21b58284123ddcebc6fb72b52fc51f0905a8aef425f8a01f1bfe6f3a467e"
)
_FROZEN_CONFIGURATION_ENFORCEMENT_SOURCE_SHA256 = (
    "2a33cc18f7eed69df3752f595d268146f47324d76e0774df6c9741557d2ee980"
)
_FROZEN_CONFIGURATION_CATALOG_SOURCE_SHA256 = (
    "4a0ab386c66682bc1e44ccb867db0410c2329ec1d1458756a91234f45036ef6e"
)


def _extension_identity():
    for module, expected in (
        (controlled, _FROZEN_CONTROLLED_SOURCE_SHA256),
        (submission, _FROZEN_SUBMISSION_SOURCE_SHA256),
        (zero_closure, _FROZEN_CLOSURE_SOURCE_SHA256),
        (closure, _FROZEN_FILLED_CLOSURE_SOURCE_SHA256),
        (terminal_catalog, _FROZEN_TERMINAL_CATALOG_SOURCE_SHA256),
        (closed_report, _FROZEN_CLOSED_REPORT_SOURCE_SHA256),
        (report_catalog, _FROZEN_REPORT_CATALOG_SOURCE_SHA256),
        (close_intent, _FROZEN_CLOSE_INTENT_SOURCE_SHA256),
        (close_index_catalog, _FROZEN_CLOSE_INDEX_CATALOG_SOURCE_SHA256),
        (configuration_schema, _FROZEN_CONFIGURATION_SCHEMA_SOURCE_SHA256),
        (configuration_enforcement, _FROZEN_CONFIGURATION_ENFORCEMENT_SOURCE_SHA256),
        (configuration_catalog, _FROZEN_CONFIGURATION_CATALOG_SOURCE_SHA256),
    ):
        path = Path(module.__file__)
        with path.open("rb") as source:
            raw = source.read(8 * 1024 * 1024 + 1)
        if (
            not 0 < len(raw) <= 8 * 1024 * 1024
            or hashlib.sha256(raw).hexdigest() != expected
        ):
            raise RuntimeError("operator_0055_extension_source_identity") from None
    controlled.statements()
    submission.statements()
    submission.restore_statements()
    closure.statements()
    closure.restore_statements()
    close_intent.statements()
    close_intent.restore_statements()
    closed_report.statements()
    closed_report.restore_statements()
    configuration_schema.statements()
    configuration_enforcement.statements()
    configuration_enforcement.restore_statements()


def _extension_owner(bind, owner):
    rows = bind.execute(
        text("""
      SELECT c.relname,c.relowner::bigint,c.relkind::text,c.relpersistence::text
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relname IN(
       'demo_account_history_native_members','demo_account_history_partition_checkpoints',
       'demo_account_observation_batches','qualification_reservation_transitions',
       'qualification_submission_outcomes','qualification_report_spool','qualification_report_projection_receipts')
    """)
    ).all()
    if (
        len(rows) != len(_EXTENSION_TABLES)
        or {row[0] for row in rows} != _EXTENSION_TABLES
        or any(tuple(row[1:]) != (owner, "r", "p") for row in rows)
    ):
        raise RuntimeError("operator_0055_extension_owner") from None


def _history_functions(bind, owner, *, new, owner_only=False):
    specs = controlled.SPECS if new else controlled.ORIGINAL_SPECS
    names = tuple(item[0] for item in specs)
    rows = bind.execute(
        text("""
      SELECT p.proname,p.proowner::bigint,p.prosecdef,p.prokind::text,
       oidvectortypes(p.proargtypes),pg_get_function_result(p.oid),p.proconfig,
       l.lanname,p.prosrc,p.provolatile::text,p.proparallel::text,p.proisstrict,p.proleakproof,
       EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
        WHERE a.grantee<>p.proowner OR a.grantor<>p.proowner OR a.is_grantable
         OR a.privilege_type<>'EXECUTE')
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      JOIN pg_language l ON l.oid=p.prolang
      WHERE n.nspname='public' AND p.proname IN(:one,:two,:three)
      ORDER BY p.proname,p.oid
    """),
        dict(zip(("one", "two", "three"), names, strict=True)),
    ).all()
    expected = sorted(
        (
            name,
            owner,
            True,
            "f",
            ", ".join(part.split(" ", 1)[1] for part in arguments.split(",")),
            returns,
            ["search_path=pg_catalog, public, pg_temp"],
            "plpgsql",
            body,
            "v",
            "u",
            False,
            False,
        )
        for name, arguments, returns, body in specs
    )
    if [tuple(row[:13]) for row in rows] != expected or (
        owner_only and any(row[13] is not False for row in rows)
    ):
        raise RuntimeError("operator_0055_history_function_drift_or_grants") from None


def _submission_function(bind, owner, *, new):
    expected = (
        submission.CURRENT_BODY_SHA256 if new else submission.ORIGINAL_BODY_SHA256
    )
    rows = bind.execute(
        text("""
      SELECT p.proowner::bigint,p.prosecdef,p.prokind::text,
       p.prorettype='pg_catalog.trigger'::regtype,p.pronargs,p.proconfig,
       l.lanname,encode(sha256(convert_to(p.prosrc,'UTF8')),'hex'),
       p.provolatile::text,p.proparallel::text,p.proisstrict,p.proleakproof
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      JOIN pg_language l ON l.oid=p.prolang
      WHERE n.nspname='public' AND p.proname='qualification_submission_binding'
    """)
    ).all()
    if [tuple(row) for row in rows] != [
        (
            owner,
            False,
            "f",
            True,
            0,
            None,
            "plpgsql",
            expected,
            "v",
            "u",
            False,
            False,
        )
    ]:
        raise RuntimeError("operator_0055_submission_function_drift") from None


def _closure_function(bind, owner, *, new, configuration=None):
    """Observe the existing physical function and all reservation guard triggers."""
    expected = closure.CURRENT_BODY_SHA256 if new else closure.ORIGINAL_BODY_SHA256
    rows = bind.execute(
        text("""
      SELECT p.oid::bigint,p.proowner::bigint,p.prosecdef,p.prokind::text,
       p.prorettype='pg_catalog.trigger'::regtype,p.pronargs,p.proconfig,
       l.lanname,encode(sha256(convert_to(p.prosrc,'UTF8')),'hex'),
       p.provolatile::text,p.proparallel::text,p.proisstrict,p.proleakproof,
       CASE WHEN p.proacl IS NULL OR cardinality(p.proacl)=0 THEN true
        WHEN array_ndims(p.proacl)<>1 OR array_lower(p.proacl,1)<>1 THEN false
        ELSE array_position(p.proacl,NULL) IS NULL END,
       (SELECT jsonb_agg(jsonb_build_array(a.grantee::bigint,a.grantor::bigint,
        a.privilege_type,a.is_grantable) ORDER BY a.grantee,a.privilege_type)
        FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a)
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      JOIN pg_language l ON l.oid=p.prolang
      WHERE n.nspname='public' AND p.proname='qualification_reservation_update'
    """)
    ).all()
    if len(rows) != 1:
        raise RuntimeError("operator_0055_native_closure_function_drift") from None
    row = tuple(rows[0])
    if (
        type(row[0]) is not int
        or not 0 < row[0] < 2**32
        or row[1:14]
        != (
            owner,
            False,
            "f",
            True,
            0,
            ["search_path=pg_catalog, pg_temp"],
            "plpgsql",
            expected,
            "v",
            "u",
            False,
            False,
            True,
        )
        or type(row[14]) is not list
        or sorted(row[14])
        != [[0, owner, "EXECUTE", False], [owner, owner, "EXECUTE", False]]
    ):
        raise RuntimeError("operator_0055_native_closure_function_drift") from None
    function_oid = row[0]
    trigger_rows = bind.execute(
        text("""
      SELECT t.tgname,t.tgtype::integer,t.tgenabled::text,t.tgisinternal,
       t.tgnargs,encode(t.tgargs,'hex'),t.tgattr::text,t.tgqual IS NULL,
       t.tgconstraint::bigint,t.tgoldtable,t.tgnewtable,pn.nspname,p.proname,
       p.proowner::bigint,p.pronargs,p.prokind::text,p.oid::bigint,
       c.relowner::bigint,c.relkind::text,c.relpersistence::text
      FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace
      JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace pn ON pn.oid=p.pronamespace
      WHERE n.nspname='public' AND c.relname='qualification_reservations'
       AND NOT t.tgisinternal ORDER BY t.tgname,t.oid
    """)
    ).all()
    kinds = {
        "qualification_reservation_no_delete": (11, "qualification_ledger_immutable"),
        "qualification_reservation_no_truncate": (34, "qualification_ledger_immutable"),
        "qualification_reservation_update_guard": (19, closure.FUNCTION),
    }
    if new:
        if type(configuration) is not dict:
            raise RuntimeError(
                "operator_0055_configuration_metadata_required"
            ) from None
        kinds[configuration_enforcement.TRIGGER] = (
            23,
            configuration_enforcement.FUNCTION,
        )
    if len(trigger_rows) != len(kinds) or {row[0] for row in trigger_rows} != set(
        kinds
    ):
        raise RuntimeError("operator_0055_native_closure_trigger_drift") from None
    for trigger in trigger_rows:
        values = tuple(trigger)
        kind, function = kinds[values[0]]
        if (
            values[1:16]
            != (
                kind,
                "O",
                False,
                0,
                "",
                "",
                True,
                0,
                None,
                None,
                "public",
                function,
                owner,
                0,
                "f",
            )
            or type(values[16]) is not int
            or not 0 < values[16] < 2**32
            or values[17:] != (owner, "r", "p")
            or (function == closure.FUNCTION and values[16] != function_oid)
        ):
            raise RuntimeError("operator_0055_native_closure_trigger_drift") from None
    if new and any(
        row[0] == configuration_enforcement.TRIGGER
        and row[16]
        != configuration["function_oids"][configuration_enforcement.FUNCTION]
        for row in trigger_rows
    ):
        raise RuntimeError("operator_0055_configuration_guard_binding_drift") from None


def _close_intent_index(bind, owner, *, present, configuration=None):
    value = bind.scalar(text(close_index_catalog.CATALOG_SQL))
    if type(present) is not bool or type(value) is not dict:
        raise RuntimeError("operator_0055_native_close_index_drift") from None
    if not present:
        if value.get("indexes") != []:
            raise RuntimeError(
                "operator_0055_native_close_index_already_exists"
            ) from None
        return
    try:
        close_index_catalog.observe_catalog(
            value,
            owner=owner,
            actor=owner,
            owner_mode=True,
            configuration_guard_oid=configuration["function_oids"][
                configuration_enforcement.FUNCTION
            ],
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError("operator_0055_native_close_index_drift") from None


def _terminal_functions(bind, owner, *, present):
    value = bind.scalar(text(terminal_catalog.CATALOG_SQL))
    if not present:
        if type(value) is not dict or value.get("functions") != []:
            raise RuntimeError("operator_0055_terminal_helpers_already_exist") from None
        return
    try:
        terminal_catalog.observe_catalog(value, owner=owner)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError("operator_0055_terminal_helper_drift_or_grants") from None


def _report_metadata(bind, owner, *, new):
    try:
        report_catalog.observe_catalog(
            bind.scalar(text(report_catalog.CATALOG_SQL)),
            owner=owner,
            actor=owner,
            owner_mode=True,
            legacy=not new,
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError(
            "operator_0055_report_function_check_trigger_acl_drift"
        ) from None


def _identity():
    if (
        schema.FROZEN_DDL_SHA256 != _FROZEN_DDL_SHA256
        or schema.FROZEN_RESTORE_SHA256 != _FROZEN_RESTORE_SHA256
    ):
        raise RuntimeError("operator_0055_source_identity") from None
    schema.statements()
    schema.restore_statements()
    _extension_identity()


def _owner(bind, expected_head):
    if bind.execute(
        text("SELECT version_num FROM public.alembic_version")
    ).scalars().all() != [expected_head]:
        raise RuntimeError("operator_0055_exact_head_required") from None
    row = bind.execute(
        text("""
      SELECT r.oid::bigint,current_user=session_user,current_setting('role')='none',r.rolsuper,
        EXISTS(SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) x
          WHERE n.nspname='public' AND x.grantee=0 AND x.privilege_type='CREATE'),
        EXISTS(SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) x
          WHERE d.defaclrole=r.oid AND x.grantee<>r.oid)
      FROM pg_roles r WHERE r.rolname=current_user
    """)
    ).one()
    if (
        type(row[0]) is not int
        or row[0] <= 0
        or tuple(row[1:]) != (True, True, True, False, False)
    ):
        raise RuntimeError("operator_0055_migration_owner") from None
    owners = bind.execute(
        text("""
      SELECT c.relname,c.relowner::bigint,c.relkind::text,c.relpersistence::text
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relname IN(
       'qualification_account_scopes','qualification_reservations','demo_account_controls',
       'demo_control_journal','demo_account_capture_events','public_runtime_raw_bundles_v1',
       'demo_collector_current_revision_lineages','demo_collector_issued_account_revisions')
    """)
    ).all()
    if (
        len(owners) != len(_PARENTS)
        or {item[0] for item in owners} != set(_PARENTS)
        or any(tuple(item[1:]) != (row[0], "r", "p") for item in owners)
    ):
        raise RuntimeError("operator_0055_parent_owner") from None
    return row[0]


def _original_control_functions(bind, owner):
    actual = bind.execute(
        text("""
      SELECT p.proname,p.proowner::bigint,p.prosecdef,p.prokind::text,
       p.prorettype='pg_catalog.trigger'::regtype,p.pronargs,p.proconfig,
       l.lanname,encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      JOIN pg_language l ON l.oid=p.prolang
      WHERE n.nspname='public' AND p.proname IN('demo_control_state_guard','demo_control_journal_guard')
      ORDER BY p.proname
    """)
    ).all()
    expected = [
        (
            "demo_control_journal_guard",
            owner,
            False,
            "f",
            True,
            0,
            None,
            "plpgsql",
            schema.ORIGINAL_JOURNAL_BODY_SHA256,
        ),
        (
            "demo_control_state_guard",
            owner,
            False,
            "f",
            True,
            0,
            None,
            "plpgsql",
            schema.ORIGINAL_STATE_BODY_SHA256,
        ),
    ]
    if [tuple(row) for row in actual] != expected:
        raise RuntimeError("operator_0055_original_control_guard_drift") from None


def _original_issuer_function(bind, owner):
    rows = bind.execute(
        text("""
      SELECT p.proowner::bigint,p.prosecdef,p.prokind::text,
       p.prorettype='pg_catalog.trigger'::regtype,p.pronargs,p.proconfig,l.lanname,
       encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      JOIN pg_language l ON l.oid=p.prolang
      WHERE n.nspname='public' AND p.proname='collector_issued_account_revision_guard_v1'
    """)
    ).all()
    if [tuple(row) for row in rows] != [
        (
            owner,
            True,
            "f",
            True,
            0,
            ["search_path=pg_catalog, pg_temp"],
            "plpgsql",
            schema.ORIGINAL_ISSUER_BODY_SHA256,
        )
    ]:
        raise RuntimeError("operator_0055_original_issuer_guard_drift") from None


def _configuration_metadata(bind, owner, *, present):
    if not present:
        found = bind.scalar(
            text("""
          SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname IN(
              'qualification_leverage_configuration_intents_v1',
              'qualification_leverage_configuration_attempts_v1',
              'qualification_leverage_configuration_results_v1'))
           OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname='public' AND p.proname IN(
              'qualification_leverage_configuration_blocked_v55',
              'qualification_leverage_configuration_initial_ledger_v55',
              'qualification_leverage_configuration_local_flat_v55',
              'qualification_leverage_configuration_guard_v55',
              'qualification_leverage_configuration_fence_v55',
              'qualification_leverage_configuration_positive_guard_v55'))
           OR EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
             JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='public' AND t.tgname='leverage_configuration_positive_guard_v55')
        """)
        )
        if found is not False:
            raise RuntimeError(
                "operator_0055_configuration_extension_already_exists"
            ) from None
        return None
    value = bind.scalar(text(configuration_catalog.CATALOG_SQL))
    return configuration_catalog.observe_catalog(
        value, owner=owner, actor=owner, owner_mode=True
    )


def _configuration_empty_owner_locked(bind, owner):
    bind.exec_driver_sql(
        "LOCK TABLE "
        + ",".join("public." + name for name in configuration_schema.TABLES)
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    actual = _configuration_metadata(bind, owner, present=True)
    if any(
        bind.scalar(text("SELECT EXISTS(SELECT 1 FROM public." + name + ")"))
        is not False
        for name in configuration_schema.TABLES
    ):
        raise RuntimeError("operator_0055_preserve_configuration_audit") from None
    return actual


def _restore_configuration_extension(bind):
    for statement in configuration_enforcement.restore_statements():
        bind.exec_driver_sql(statement)
    for name in reversed(configuration_schema.TABLES):
        for suffix in ("_fence", "_guard", "_no_truncate"):
            bind.exec_driver_sql("DROP TRIGGER " + name + suffix + " ON public." + name)
    for name, arguments, _returns, _language, _volatility, _sql in reversed(
        configuration_schema.FUNCTION_IDENTITIES
    ):
        bind.exec_driver_sql("DROP FUNCTION public." + name + "(" + arguments + ")")
    for name in reversed(configuration_schema.TABLES):
        bind.exec_driver_sql("DROP TABLE public." + name)


def upgrade():
    _identity()
    bind = op.get_bind()
    bind.exec_driver_sql(_LOCK)
    owner = _owner(bind, "0054")
    _original_control_functions(bind, owner)
    _original_issuer_function(bind, owner)
    bind.exec_driver_sql(_EXTENSION_LOCK)
    _extension_owner(bind, owner)
    _history_functions(bind, owner, new=False)
    _submission_function(bind, owner, new=False)
    _closure_function(bind, owner, new=False)
    _close_intent_index(bind, owner, present=False)
    _terminal_functions(bind, owner, present=False)
    _report_metadata(bind, owner, new=False)
    _configuration_metadata(bind, owner, present=False)
    if bind.scalar(
        text("""
      SELECT to_regclass('public.demo_control_clear_acknowledgements_v2') IS NOT NULL
       OR to_regclass('public.demo_policy_counter_initializations_v1') IS NOT NULL
       OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN(
         'control_clear_ack_guard_v2','control_clear_ack_commit_guard_v2',
         'policy_counter_initialization_guard_v1','policy_counter_initialization_commit_guard_v1',
         'control_operator_ack_immutable_v55',
         'account_history_partition_current55_v2_lock',
         'account_history_partition_current55_v2_append',
         'account_history_original_frame_current55_v2_read'))
    """)
    ):
        raise RuntimeError("operator_0055_existing_schema") from None
    for statement in schema.statements():
        bind.exec_driver_sql(statement)
    for statement in controlled.statements():
        bind.exec_driver_sql(statement)
    for statement in submission.statements():
        bind.exec_driver_sql(statement)
    for statement in closure.statements():
        bind.exec_driver_sql(statement)
    for statement in close_intent.statements():
        bind.exec_driver_sql(statement)
    for statement in closed_report.statements():
        bind.exec_driver_sql(statement)
    for statement in configuration_schema.statements():
        bind.exec_driver_sql(statement)
    for statement in configuration_enforcement.statements():
        bind.exec_driver_sql(statement)
    configuration = _configuration_metadata(bind, owner, present=True)
    _history_functions(bind, owner, new=False)
    _history_functions(bind, owner, new=True, owner_only=True)
    _submission_function(bind, owner, new=True)
    _closure_function(bind, owner, new=True, configuration=configuration)
    _close_intent_index(bind, owner, present=True, configuration=configuration)
    _terminal_functions(bind, owner, present=True)
    _report_metadata(bind, owner, new=True)
    # No actor, reader, default, raw-table or inherited grants are installed.


def downgrade():
    _identity()
    bind = op.get_bind()
    bind.exec_driver_sql(_LOCK)
    owner = _owner(bind, "0055")
    bind.exec_driver_sql(_EXTENSION_LOCK)
    configuration = _configuration_empty_owner_locked(bind, owner)
    _extension_owner(bind, owner)
    _history_functions(bind, owner, new=False)
    _history_functions(bind, owner, new=True, owner_only=True)
    _submission_function(bind, owner, new=True)
    _closure_function(bind, owner, new=True, configuration=configuration)
    _close_intent_index(bind, owner, present=True, configuration=configuration)
    _terminal_functions(bind, owner, present=True)
    _report_metadata(bind, owner, new=True)
    bind.exec_driver_sql(
        "LOCK TABLE "
        + ",".join("public." + name for name in _NEW_TABLES)
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    rows = bind.execute(
        text("""
      SELECT c.relname,c.relowner::bigint,c.relkind::text,c.relpersistence::text,
       EXISTS(SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x WHERE x.grantee<>c.relowner)
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relname IN(
       'demo_control_clear_acknowledgements_v2','demo_policy_counter_initializations_v1')
    """)
    ).all()
    if (
        len(rows) != 2
        or {row[0] for row in rows} != set(_NEW_TABLES)
        or any(tuple(row[1:]) != (owner, "r", "p", False) for row in rows)
        or bind.scalar(
            text("""
      SELECT EXISTS(SELECT 1 FROM public.demo_control_clear_acknowledgements_v2)
       OR EXISTS(SELECT 1 FROM public.demo_policy_counter_initializations_v1)
       OR EXISTS(SELECT 1 FROM public.demo_control_journal WHERE action='clear_stop')
       OR EXISTS(SELECT 1 FROM public.demo_collector_issued_account_revisions issued
        JOIN public.public_runtime_raw_bundles_v1 b ON b.bundle_id=issued.hwm_custody_id
        WHERE convert_from(b.bundle_raw,'UTF8')::jsonb->>'schema_version'
          ='ctcc.native_owned_controlled_balance_peak_record.v2')
    """)
        )
    ):
        raise RuntimeError("operator_0055_preserve_audit_and_revoke_grants") from None
    if bind.scalar(
        text("""
      SELECT EXISTS(SELECT 1 FROM public.qualification_report_spool WHERE eligible)
       OR EXISTS(SELECT 1 FROM public.qualification_report_projection_receipts)
    """)
    ):
        raise RuntimeError(
            "operator_0055_preserve_native_report_projection_history"
        ) from None
    if bind.scalar(
        text("""
      SELECT EXISTS(SELECT 1 FROM public.qualification_submission_outcomes
       WHERE capture_json::jsonb->>'version'='ctcc.native_controlled_close_intent.v1')
    """)
    ):
        raise RuntimeError("operator_0055_preserve_native_close_intent") from None
    if bind.scalar(
        text("""
      SELECT EXISTS(
       SELECT 1 FROM public.qualification_submission_outcomes o
       JOIN public.qualification_reservations r ON r.reservation_id=o.reservation_id
       WHERE r.request_json::jsonb->>'contract_version'='ctcc-reservation-request-native-v4')
    """)
    ):
        raise RuntimeError("operator_0055_preserve_native_submission_history") from None
    if bind.scalar(
        text("""
      SELECT EXISTS(
       SELECT 1 FROM public.qualification_reservations
       WHERE request_json::jsonb->>'contract_version'='ctcc-reservation-request-native-v4')
    """)
    ):
        raise RuntimeError(
            "operator_0055_preserve_native_reservation_tombstones"
        ) from None
    _restore_configuration_extension(bind)
    for statement in close_intent.restore_statements():
        bind.exec_driver_sql(statement)
    _close_intent_index(bind, owner, present=False)
    for statement in closed_report.restore_statements():
        bind.exec_driver_sql(statement)
    _report_metadata(bind, owner, new=False)
    for statement in closure.restore_original_statements():
        bind.exec_driver_sql(statement)
    _terminal_functions(bind, owner, present=False)
    for statement in submission.restore_statements():
        bind.exec_driver_sql(statement)
    for statement in controlled.DROP_SQL:
        bind.exec_driver_sql(statement)
    _history_functions(bind, owner, new=False)
    _submission_function(bind, owner, new=False)
    _closure_function(bind, owner, new=False)
    for statement in schema.restore_statements():
        bind.exec_driver_sql(statement)
    bind.exec_driver_sql(
        "ALTER TABLE public.demo_control_journal DROP CONSTRAINT ck_demo_control_journal_action"
    )
    bind.exec_driver_sql(
        "ALTER TABLE public.demo_control_journal ADD CONSTRAINT ck_demo_control_journal_action CHECK(action IN ('acquire','renew','arm_requested','disarm','estop','rebind','release'))"
    )
    for name in _NEW_TABLES:
        bind.exec_driver_sql("DROP TABLE public." + name)
    for name in _NEW_FUNCTIONS:
        bind.exec_driver_sql("DROP FUNCTION public." + name + "()")
    _original_control_functions(bind, owner)
    _original_issuer_function(bind, owner)
