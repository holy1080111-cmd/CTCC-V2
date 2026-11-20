"""Preserve normal Demo holds and add a separate, disarmed Live intent ledger.

Revision ID: 0058
Revises: 0057

No runtime role receives a grant, no control is created or armed, and no
retained reservation, intent or evidence row is changed by this migration.
"""

from alembic import op

from app.domain import native_filled_flat_closure_schema_v55 as previous
from app.domain import native_live_financial_epoch_schema_v1 as financial
from app.domain import native_live_ledger_schema_v1 as live
from app.domain import native_protective_hold_schema_v58 as hold
from app.domain import native_protective_report_schema_v58 as report
from app.domain import native_submission_binding_schema_v58 as submission

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None

_LOCK = (
    "LOCK TABLE public.qualification_account_scopes, "
    "public.qualification_reservations, public.qualification_reservation_transitions, "
    "public.qualification_submission_outcomes IN ACCESS EXCLUSIVE MODE NOWAIT"
)


def _require_guard(pin):
    op.execute(f"""
 DO $check$ BEGIN
 IF NOT EXISTS (
  SELECT 1 FROM pg_catalog.pg_proc p
  JOIN pg_catalog.pg_class c ON c.oid='public.qualification_reservations'::regclass
  JOIN pg_catalog.pg_language l ON l.oid=p.prolang
  WHERE p.oid=pg_catalog.to_regprocedure('public.qualification_reservation_update()')
   AND p.proowner=c.relowner AND l.lanname='plpgsql'
   AND p.prorettype='pg_catalog.trigger'::regtype AND p.pronargs=0
   AND NOT p.proretset AND NOT p.prosecdef AND NOT p.proisstrict AND NOT p.proleakproof
   AND p.prokind='f' AND p.provolatile='v' AND p.proparallel='u' AND p.prosupport=0
   AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]
   AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex')='{pin}'
 ) THEN RAISE EXCEPTION 'protected58_original_guard_changed'; END IF;
 END $check$;
 """)


def _require_submission_guard(pin):
    op.execute(f"""
 DO $check$ BEGIN
 IF NOT EXISTS (
  SELECT 1 FROM pg_catalog.pg_proc p
  JOIN pg_catalog.pg_class c ON c.oid='public.qualification_submission_outcomes'::regclass
  JOIN pg_catalog.pg_language l ON l.oid=p.prolang
  JOIN pg_catalog.pg_trigger t ON t.tgrelid=c.oid AND t.tgfoid=p.oid
  WHERE p.oid=pg_catalog.to_regprocedure('public.qualification_submission_binding()')
   AND p.proowner=c.relowner AND l.lanname='plpgsql'
   AND p.prorettype='pg_catalog.trigger'::regtype AND p.pronargs=0
   AND NOT p.proretset AND NOT p.prosecdef AND NOT p.proisstrict AND NOT p.proleakproof
   AND p.prokind='f' AND p.provolatile='v' AND p.proparallel='u' AND p.prosupport=0
   AND p.proconfig IS NULL
   AND t.tgname='qualification_submission_binding_guard' AND t.tgenabled='O'
   AND t.tgtype=7 AND NOT t.tgisinternal AND t.tgnargs=0 AND t.tgconstraint=0
   AND t.tgqual IS NULL AND t.tgoldtable IS NULL AND t.tgnewtable IS NULL
   AND t.tgattr::text='' AND pg_catalog.octet_length(t.tgargs)=0
   AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex')='{pin}'
 ) THEN RAISE EXCEPTION 'protected58_submission_guard_changed'; END IF;
 END $check$;
 """)


def _require_financial_guard():
    from hashlib import sha256

    pin = sha256(financial.GUARD_SQL.split("$body$")[1].encode()).hexdigest()
    op.execute(f"""
 DO $check$ BEGIN
 IF NOT EXISTS (
  SELECT 1 FROM pg_catalog.pg_proc p
  JOIN pg_catalog.pg_class c ON c.oid='public.native_live_financial_epochs_v1'::regclass
  JOIN pg_catalog.pg_language l ON l.oid=p.prolang
  WHERE p.oid=pg_catalog.to_regprocedure('public.native_live_financial_guard_v1()')
   AND p.proowner=c.relowner AND l.lanname='plpgsql'
   AND p.prorettype='pg_catalog.trigger'::regtype AND p.pronargs=0
   AND NOT p.proretset AND NOT p.prosecdef AND NOT p.proisstrict AND NOT p.proleakproof
   AND p.prokind='f' AND p.provolatile='v' AND p.proparallel='u' AND p.prosupport=0
   AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]
   AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex')='{pin}'
   AND (SELECT count(*) FROM pg_catalog.pg_trigger t
        WHERE t.tgfoid=p.oid AND t.tgrelid IN (
          'public.native_live_financial_epochs_v1'::regclass,
          'public.native_live_financial_samples_v1'::regclass)
          AND t.tgname=((SELECT relname FROM pg_catalog.pg_class
                       WHERE oid=t.tgrelid)||'_immutable')
          AND t.tgenabled='O' AND t.tgtype=31 AND NOT t.tgisinternal
          AND t.tgnargs=0 AND t.tgconstraint=0 AND t.tgqual IS NULL
          AND t.tgoldtable IS NULL AND t.tgnewtable IS NULL
          AND t.tgattr::text='' AND pg_catalog.octet_length(t.tgargs)=0)=2
 ) THEN RAISE EXCEPTION 'protected58_financial_guard_changed'; END IF;
 END $check$;
 """)


def _require_report_guard(sql):
    from hashlib import sha256

    pin = sha256(sql.split("$ctcc$")[1].encode()).hexdigest()
    op.execute(f"""
 DO $check$ BEGIN
 IF NOT EXISTS (
  SELECT 1 FROM pg_catalog.pg_proc p
  JOIN pg_catalog.pg_class c ON c.oid='public.qualification_submission_outcomes'::regclass
  JOIN pg_catalog.pg_language l ON l.oid=p.prolang
  WHERE p.oid=pg_catalog.to_regprocedure('public.qualification_native_report_eligible_v55(text,text,text,text)')
   AND p.proowner=c.relowner AND l.lanname='plpgsql'
   AND p.prorettype='pg_catalog.bool'::regtype AND p.pronargs=4
   AND NOT p.proretset AND NOT p.prosecdef AND NOT p.proisstrict AND NOT p.proleakproof
   AND p.prokind='f' AND p.provolatile='v' AND p.proparallel='u' AND p.prosupport=0
   AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]
   AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex')='{pin}'
 ) THEN RAISE EXCEPTION 'protected58_report_eligible_changed'; END IF;
 END $check$;
 """)


def upgrade():
    op.execute(_LOCK)
    _require_guard(previous.CURRENT_BODY_SHA256)
    _require_submission_guard(submission.ANCESTOR_BODY_SHA256)
    _require_report_guard(report.ancestor.ELIGIBLE_SQL)
    for statement in hold.statements():
        op.execute(statement)
    op.execute(hold.CURRENT_SQL)
    _require_guard(hold.CURRENT_BODY_SHA256)
    for statement in submission.statements():
        op.execute(statement)
    _require_submission_guard(submission.CURRENT_BODY_SHA256)
    for statement in report.statements():
        op.execute(statement)
    _require_report_guard(report.ELIGIBLE_SQL)
    for statement in live.UPGRADE_SQL:
        op.execute(statement)
    for statement in financial.UPGRADE_SQL:
        op.execute(statement)
    _require_financial_guard()


def downgrade():
    op.execute(_LOCK)
    op.execute(
        "LOCK TABLE "
        + ", ".join("public." + table for table in (*live.TABLES, *financial.TABLES))
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    _require_guard(hold.CURRENT_BODY_SHA256)
    _require_submission_guard(submission.CURRENT_BODY_SHA256)
    _require_report_guard(report.ELIGIBLE_SQL)
    _require_financial_guard()
    op.execute("""
 DO $check$ BEGIN
 IF EXISTS(SELECT 1 FROM public.qualification_submission_outcomes
     WHERE capture_json::jsonb->>'version' IN (
      'ctcc.native_protected_hold.v1','ctcc.native_protective_flat_closure.v1',
      'ctcc.native_protected_hold_resources_retired.v1',
      'ctcc.native_original_oco_forensics_readback.v1',
      'ctcc.native_original_oco_cleanup.v1',
      'ctcc.native_original_oco_report_ready.v1'))
 OR EXISTS(SELECT 1 FROM public.native_live_controls_v1)
 OR EXISTS(SELECT 1 FROM public.native_live_reservations_v1)
 OR EXISTS(SELECT 1 FROM public.native_live_intents_v1)
 OR EXISTS(SELECT 1 FROM public.native_live_transitions_v1)
 OR EXISTS(SELECT 1 FROM public.native_live_financial_epochs_v1)
 OR EXISTS(SELECT 1 FROM public.native_live_financial_samples_v1)
 OR EXISTS(
  SELECT 1 FROM pg_catalog.pg_class c
  CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) a
  WHERE c.oid IN ('public.native_live_controls_v1'::regclass,
                 'public.native_live_reservations_v1'::regclass,
                 'public.native_live_intents_v1'::regclass,
                 'public.native_live_transitions_v1'::regclass,
                 'public.native_live_financial_epochs_v1'::regclass,
                 'public.native_live_financial_samples_v1'::regclass)
   AND a.grantee<>c.relowner
 ) OR EXISTS(
  SELECT 1 FROM pg_catalog.pg_proc p
  CROSS JOIN LATERAL pg_catalog.aclexplode(
    COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
  WHERE p.pronamespace='public'::regnamespace
    AND p.proname IN ('native_live_control_fence_v1','native_live_inhibit_v1',
      'native_live_disarm_v1','native_live_reservation_guard_v1',
      'native_live_data_guard_v1','native_live_financial_guard_v1')
    AND a.grantee<>p.proowner
 ) THEN RAISE EXCEPTION 'protected58_preserve_history_and_revoke_grants'; END IF;
 END $check$;
 """)
    op.execute(previous.CURRENT_SQL)
    _require_guard(previous.CURRENT_BODY_SHA256)
    for statement in report.restore_statements():
        op.execute(statement)
    _require_report_guard(report.ancestor.ELIGIBLE_SQL)
    for statement in submission.restore_statements():
        op.execute(statement)
    _require_submission_guard(submission.ANCESTOR_BODY_SHA256)
    for statement in hold.restore_statements():
        op.execute(statement)
    # Dependency order is explicit; a remaining function/reference blocks
    # rollback. CASCADE and removal of retained rows are never used.
    for statement in financial.DOWNGRADE_SQL:
        op.execute(statement)
    for statement in live.DOWNGRADE_SQL:
        op.execute(statement)
