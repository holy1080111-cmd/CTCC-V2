"""Exact unsigned native root identity; retain legacy witness record bytes.

Revision ID: 0047
Revises: 0046

NUMERIC has no precision/scale typmod: forged fractions must be rejected, never
rounded by a column coercion. Existing rows/hashes, function OIDs/ACLs and the
immutable transition machine are retained. Downgrade requires an empty witness.
"""

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

DEVICE_MAX = "18446744073709551615"
INODE_MAX = "340282366920938463463374607431768211455"
_DEVICE_LEXEME = "^(0|[1-9][0-9]{0,19})$"
_INODE_LEXEME = "^[1-9][0-9]{0,38}$"

# Exact original DB0024 SQL, embedded rather than editing history or dynamically
# loading another migration at runtime. All record-hash/transition SQL stays so.
_LEGACY_INSERT = """
      CREATE FUNCTION public_receipt_witness_insert_guard() RETURNS trigger AS $$
      DECLARE p public.public_receipt_witness_revisions%ROWTYPE;
              j JSONB; old_j JSONB; lock_key BIGINT;
      BEGIN
        lock_key := ('x'||substr(NEW.journal_key,1,16))::bit(64)::bigint;
        PERFORM pg_catalog.pg_advisory_xact_lock(lock_key);
        SELECT * INTO p FROM public.public_receipt_witness_revisions
          WHERE journal_key=NEW.journal_key ORDER BY revision DESC LIMIT 1;
        IF encode(pg_catalog.sha256(pg_catalog.convert_to(NEW.checkpoint_json,'UTF8')),'hex')
             IS DISTINCT FROM NEW.checkpoint_sha256 THEN
          RAISE EXCEPTION 'public_witness_checkpoint_hash_denied';
        END IF;
        j := NEW.checkpoint_json::jsonb;
        IF pg_catalog.jsonb_typeof(j) IS DISTINCT FROM 'object'
          OR j->>'schema_version' IS DISTINCT FROM 'ctcc.public.journal_checkpoint.v1'
          OR j->>'genesis_sha256' IS DISTINCT FROM NEW.journal_key
          OR j->>'head_sha256' IS NULL
          OR j->>'head_sha256' !~ '^[a-f0-9]{64}$'
          OR j->>'attempt_head_sha256' IS NULL
          OR j->>'attempt_head_sha256' !~ '^[a-f0-9]{64}$'
          OR j->>'sequence' IS NULL
          OR j->>'attempt_sequence' IS NULL
          OR (j->>'root_device')::bigint IS DISTINCT FROM NEW.root_device
          OR (j->>'root_inode')::bigint IS DISTINCT FROM NEW.root_inode THEN
          RAISE EXCEPTION 'public_witness_checkpoint_identity_denied';
        END IF;
        IF p.revision IS NULL THEN
          IF NEW.revision <> 0 OR NEW.transition <> 'initialize'
            OR NEW.state <> 'idle' OR NEW.operation_id IS NOT NULL
            OR NEW.plan_sha256 IS NOT NULL OR NEW.attempt_outcome IS NOT NULL
            OR NEW.previous_record_sha256 IS NOT NULL
            OR (j->>'sequence')::bigint <> 0
            OR (j->>'attempt_sequence')::bigint <> 0
            OR j->>'head_sha256' IS DISTINCT FROM NEW.journal_key
            OR j->>'attempt_head_sha256' IS DISTINCT FROM NEW.journal_key THEN
            RAISE EXCEPTION 'public_witness_initialize_denied';
          END IF;
        ELSE
          IF NEW.revision <> p.revision+1 OR NEW.revision > 8192
            OR NEW.previous_record_sha256 IS DISTINCT FROM p.record_sha256
            OR NEW.journal_id IS DISTINCT FROM p.journal_id
            OR NEW.root_device IS DISTINCT FROM p.root_device
            OR NEW.root_inode IS DISTINCT FROM p.root_inode
            OR NEW.operation_id IS NULL
            OR NEW.plan_sha256 IS NULL
            OR NEW.plan_sha256 !~ '^[a-f0-9]{64}$' THEN
            RAISE EXCEPTION 'public_witness_revision_denied';
          END IF;
          old_j := p.checkpoint_json::jsonb;
          IF NEW.transition='open_attempt' THEN
            IF p.state <> 'idle' OR NEW.state <> 'open'
              OR NEW.attempt_outcome IS NOT NULL
              OR NEW.checkpoint_json IS DISTINCT FROM p.checkpoint_json THEN
              RAISE EXCEPTION 'public_witness_open_denied';
            END IF;
          ELSIF NEW.transition='append_attempt' THEN
            IF p.state <> 'open' OR NEW.state <> 'attempt_anchored'
              OR NEW.operation_id IS DISTINCT FROM p.operation_id
              OR NEW.plan_sha256 IS DISTINCT FROM p.plan_sha256
              OR NEW.attempt_outcome IS NULL
              OR NEW.attempt_outcome NOT IN
                ('completed_collection','rejected','incomplete')
              OR (j->>'attempt_sequence')::bigint <>
                (old_j->>'attempt_sequence')::bigint+1
              OR j->>'attempt_head_sha256' IS NOT DISTINCT FROM
                old_j->>'attempt_head_sha256'
              OR j->>'sequence' IS DISTINCT FROM old_j->>'sequence'
              OR j->>'head_sha256' IS DISTINCT FROM old_j->>'head_sha256' THEN
              RAISE EXCEPTION 'public_witness_attempt_denied';
            END IF;
          ELSIF NEW.transition='append_capture' THEN
            IF p.state <> 'attempt_anchored'
              OR p.attempt_outcome <> 'completed_collection'
              OR NEW.state <> 'idle'
              OR NEW.operation_id IS DISTINCT FROM p.operation_id
              OR NEW.plan_sha256 IS DISTINCT FROM p.plan_sha256
              OR NEW.attempt_outcome IS DISTINCT FROM p.attempt_outcome
              OR (j->>'sequence')::bigint <> (old_j->>'sequence')::bigint+1
              OR j->>'head_sha256' IS NOT DISTINCT FROM old_j->>'head_sha256'
              OR j->>'attempt_sequence' IS DISTINCT FROM old_j->>'attempt_sequence'
              OR j->>'attempt_head_sha256' IS DISTINCT FROM
                old_j->>'attempt_head_sha256' THEN
              RAISE EXCEPTION 'public_witness_capture_denied';
            END IF;
          ELSIF NEW.transition='close_rejected_attempt' THEN
            IF p.state <> 'attempt_anchored'
              OR p.attempt_outcome NOT IN ('rejected','incomplete')
              OR NEW.state <> 'idle'
              OR NEW.operation_id IS DISTINCT FROM p.operation_id
              OR NEW.plan_sha256 IS DISTINCT FROM p.plan_sha256
              OR NEW.attempt_outcome IS DISTINCT FROM p.attempt_outcome
              OR NEW.checkpoint_json IS DISTINCT FROM p.checkpoint_json THEN
              RAISE EXCEPTION 'public_witness_close_denied';
            END IF;
          ELSE
            RAISE EXCEPTION 'public_witness_transition_denied';
          END IF;
        END IF;
        NEW.record_sha256 := encode(pg_catalog.sha256(pg_catalog.convert_to(
          pg_catalog.concat_ws(chr(31),
            NEW.journal_key,NEW.revision::text,NEW.journal_id,
            NEW.root_device::text,NEW.root_inode::text,NEW.transition,
            NEW.state,coalesce(NEW.operation_id,''),coalesce(NEW.plan_sha256,''),
            coalesce(NEW.attempt_outcome,''),
            coalesce(NEW.previous_record_sha256,''),
            NEW.checkpoint_sha256,NEW.checkpoint_json), 'UTF8')),'hex');
        RETURN NEW;
      END; $$ LANGUAGE plpgsql VOLATILE
        SET search_path=pg_catalog,pg_temp
    """

_LEGACY_APPEND = """
      CREATE FUNCTION public_receipt_witness_append(p_record JSONB)
      RETURNS void AS $$
      BEGIN
        IF pg_catalog.jsonb_typeof(p_record) IS DISTINCT FROM 'object' THEN
          RAISE EXCEPTION 'public_witness_record_invalid';
        END IF;
        INSERT INTO public.public_receipt_witness_revisions (
          journal_key,revision,journal_id,root_device,root_inode,
          transition,state,operation_id,plan_sha256,attempt_outcome,
          previous_record_sha256,checkpoint_json,checkpoint_sha256,record_sha256
        ) VALUES (
          p_record->>'journal_key',
          (p_record->>'revision')::bigint,
          p_record->>'journal_id',
          (p_record->>'root_device')::bigint,
          (p_record->>'root_inode')::bigint,
          p_record->>'transition',p_record->>'state',
          p_record->>'operation_id',p_record->>'plan_sha256',
          p_record->>'attempt_outcome',p_record->>'previous_record_sha256',
          p_record->>'checkpoint_json',p_record->>'checkpoint_sha256',
          repeat('0',64)
        );
      END; $$ LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp
    """


def _replace_once(value, before, after):
    if value.count(before) != 1:
        raise RuntimeError("native_identity_migration_source_mismatch")
    return value.replace(before, after, 1)


def _replace_function(sql):
    return _replace_once(sql, "CREATE FUNCTION ", "CREATE OR REPLACE FUNCTION public.")


_RAW_IDENTITY_GUARD = f"""
        source_json := NEW.checkpoint_json::json;
        IF pg_catalog.json_typeof(source_json) IS DISTINCT FROM 'object' THEN
          RAISE EXCEPTION 'public_witness_native_identity_denied';
        END IF;
        IF pg_catalog.json_typeof(source_json->'root_device') IS DISTINCT FROM 'number'
          OR pg_catalog.json_typeof(source_json->'root_inode') IS DISTINCT FROM 'number'
          OR source_json->>'root_device' !~ '{_DEVICE_LEXEME}'
          OR source_json->>'root_inode' !~ '{_INODE_LEXEME}'
          OR NEW.root_device::text !~ '{_DEVICE_LEXEME}'
          OR NEW.root_inode::text !~ '{_INODE_LEXEME}'
          OR (SELECT count(*) FROM pg_catalog.json_each(source_json)
                WHERE key='root_device') <> 1
          OR (SELECT count(*) FROM pg_catalog.json_each(source_json)
                WHERE key='root_inode') <> 1
        THEN RAISE EXCEPTION 'public_witness_native_identity_denied'; END IF;
        IF (source_json->>'root_device')::numeric NOT BETWEEN 0 AND {DEVICE_MAX}
          OR (source_json->>'root_inode')::numeric NOT BETWEEN 1 AND {INODE_MAX}
          OR NEW.root_device NOT BETWEEN 0 AND {DEVICE_MAX}
          OR NEW.root_inode NOT BETWEEN 1 AND {INODE_MAX}
        THEN RAISE EXCEPTION 'public_witness_native_identity_denied'; END IF;
"""
_JSONB_IDENTITY_GUARD = f"""
        -- JSONB no longer retains original wire lexemes (e.g. 1e0 can be 1).
        -- Validate its number type/canonical rendered integer; original numeric
        -- lexemes are separately enforced in checkpoint_json TEXT by the trigger.
        IF pg_catalog.jsonb_typeof(p_record->'root_device') IS DISTINCT FROM 'number'
          OR pg_catalog.jsonb_typeof(p_record->'root_inode') IS DISTINCT FROM 'number'
          OR p_record->>'root_device' !~ '{_DEVICE_LEXEME}'
          OR p_record->>'root_inode' !~ '{_INODE_LEXEME}'
        THEN RAISE EXCEPTION 'public_witness_native_identity_denied'; END IF;
        IF (p_record->>'root_device')::numeric NOT BETWEEN 0 AND {DEVICE_MAX}
          OR (p_record->>'root_inode')::numeric NOT BETWEEN 1 AND {INODE_MAX}
        THEN RAISE EXCEPTION 'public_witness_native_identity_denied'; END IF;
"""
_INSERT = _replace_function(_LEGACY_INSERT)
_INSERT = _replace_once(
    _INSERT,
    "j JSONB; old_j JSONB; lock_key BIGINT;",
    "j JSONB; source_json JSON; old_j JSONB; lock_key BIGINT;",
)
_INSERT = _replace_once(
    _INSERT,
    "        j := NEW.checkpoint_json::jsonb;",
    _RAW_IDENTITY_GUARD + "        j := NEW.checkpoint_json::jsonb;",
)
for _field in ("root_device", "root_inode"):
    _INSERT = _replace_once(
        _INSERT, f"(j->>'{_field}')::bigint", f"(j->>'{_field}')::numeric"
    )
_APPEND = _replace_function(_LEGACY_APPEND)
_APPEND = _replace_once(
    _APPEND,
    "        INSERT INTO public.public_receipt_witness_revisions (",
    _JSONB_IDENTITY_GUARD
    + "        INSERT INTO public.public_receipt_witness_revisions (",
)
for _field in ("root_device", "root_inode"):
    _APPEND = _replace_once(
        _APPEND, f"(p_record->>'{_field}')::bigint", f"(p_record->>'{_field}')::numeric"
    )


def upgrade():
    op.execute(
        "LOCK TABLE public.public_receipt_witness_revisions IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
      ALTER TABLE public.public_receipt_witness_revisions
        ALTER COLUMN root_device TYPE NUMERIC USING root_device::numeric,
        ALTER COLUMN root_inode TYPE NUMERIC USING root_inode::numeric
    """)
    op.execute(f"""
      ALTER TABLE public.public_receipt_witness_revisions
      ADD CONSTRAINT ck_public_receipt_witness_revisions_native_identity_bounds CHECK (
        root_device BETWEEN 0 AND {DEVICE_MAX} AND root_device=trunc(root_device)
        AND root_inode BETWEEN 1 AND {INODE_MAX} AND root_inode=trunc(root_inode))
    """)
    op.execute(_INSERT)
    op.execute(_APPEND)


def downgrade():
    op.execute(
        "LOCK TABLE public.public_receipt_witness_revisions IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.public_receipt_witness_revisions) THEN
          RAISE EXCEPTION 'public_witness_wide_identity_downgrade_requires_empty';
        END IF;
      END $$
    """)
    op.execute(
        "ALTER TABLE public.public_receipt_witness_revisions DROP CONSTRAINT ck_public_receipt_witness_revisions_native_identity_bounds"
    )
    op.execute("""
      ALTER TABLE public.public_receipt_witness_revisions
        ALTER COLUMN root_device TYPE BIGINT USING root_device::bigint,
        ALTER COLUMN root_inode TYPE BIGINT USING root_inode::bigint
    """)
    op.execute(_replace_function(_LEGACY_INSERT))
    op.execute(_replace_function(_LEGACY_APPEND))
