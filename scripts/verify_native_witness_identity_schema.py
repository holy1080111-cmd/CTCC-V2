"""DB0047 catalog pin. Native PG17 constraint baseline is not yet sealed.

The pending value deliberately cannot match a real SHA256 catalog readback.
Filling it requires reviewed native DDL metadata and the targeted PostgreSQL
cases; source-only unit tests are not native schema acceptance.
"""

import re


def require_native_witness_identity_baseline() -> None:
    """A pending template, including an identical caller DTO, cannot pass."""
    digest = NATIVE_WITNESS_IDENTITY_SCHEMA["constraint"][-1]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RuntimeError("native_witness_identity_schema_baseline_unsealed")


NATIVE_WITNESS_IDENTITY_SCHEMA_SQL = """
SELECT jsonb_build_object(
 'table_present',pg_catalog.to_regclass('public.public_receipt_witness_revisions') IS NOT NULL,
 'table_shape',COALESCE((SELECT relkind='r' AND relpersistence='p'
   AND NOT relrowsecurity AND NOT relforcerowsecurity FROM pg_catalog.pg_class
   WHERE oid=pg_catalog.to_regclass('public.public_receipt_witness_revisions')),false),
 'columns',COALESCE((SELECT jsonb_agg(jsonb_build_array(attname,
   pg_catalog.format_type(atttypid,atttypmod),atttypmod,attnotnull,
   attidentity='',attgenerated='') ORDER BY attnum)
   FROM pg_catalog.pg_attribute WHERE attrelid=pg_catalog.to_regclass(
   'public.public_receipt_witness_revisions') AND attname IN ('root_device','root_inode')
   AND attnum>0 AND NOT attisdropped),'[]'::jsonb),
 'functions',COALESCE((SELECT jsonb_agg(jsonb_build_array(proname,
   pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(prosrc,'UTF8')),'hex'),
   prosecdef,proretset,pg_catalog.oidvectortypes(proargtypes),provolatile::text,
   proconfig=ARRAY['search_path=pg_catalog, pg_temp'],
   proowner=(SELECT relowner FROM pg_catalog.pg_class WHERE oid=
     pg_catalog.to_regclass('public.public_receipt_witness_revisions')),
   NOT proisstrict AND proparallel='u' AND NOT proleakproof AND prokind='f'
     AND prosupport=0 AND (SELECT lanname FROM pg_catalog.pg_language WHERE oid=prolang)
       =CASE WHEN proname='public_receipt_witness_read' THEN 'sql' ELSE 'plpgsql' END
     AND prorettype=CASE proname
       WHEN 'public_receipt_witness_append' THEN pg_catalog.to_regtype('void')
       WHEN 'public_receipt_witness_read' THEN
         pg_catalog.to_regtype('public.public_receipt_witness_revisions')
       ELSE pg_catalog.to_regtype('trigger') END,
   NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(proacl,
     pg_catalog.acldefault('f',proowner))) a
     WHERE a.grantee=0 AND a.privilege_type='EXECUTE')) ORDER BY proname)
   FROM pg_catalog.pg_proc WHERE pronamespace='public'::regnamespace
   AND proname IN ('public_receipt_witness_append','public_receipt_witness_insert_guard',
     'public_receipt_witness_immutable','public_receipt_witness_read')),'[]'::jsonb),
 'triggers',COALESCE((SELECT jsonb_agg(jsonb_build_array(tgname,tgenabled::text,
   tgtype::integer,p.proname,tgqual IS NULL AND tgconstraint=0 AND tgnargs=0
   AND pg_catalog.octet_length(tgargs)=0 AND tgattr::text=''
   AND tgoldtable IS NULL AND tgnewtable IS NULL
   AND p.pronamespace='public'::regnamespace) ORDER BY tgname)
   FROM pg_catalog.pg_trigger g JOIN pg_catalog.pg_proc p ON p.oid=g.tgfoid
   WHERE tgrelid=pg_catalog.to_regclass('public.public_receipt_witness_revisions')
   AND NOT tgisinternal),'[]'::jsonb),
 'constraint',COALESCE((SELECT jsonb_build_array(conname,contype::text,
   convalidated,condeferrable,condeferred,
   pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
     pg_catalog.pg_get_constraintdef(oid,true),'UTF8')),'hex'))
   FROM pg_catalog.pg_constraint WHERE conrelid=pg_catalog.to_regclass(
   'public.public_receipt_witness_revisions') AND conname=
   'ck_public_receipt_witness_revisions_native_identity_bounds'),'[]'::jsonb))
"""

# Function bodies are derived from the exact migration SQL. The constraint
# deparser hash remains absent until the independent native diagnostic review.
NATIVE_WITNESS_IDENTITY_SCHEMA = {
    "table_present": True,
    "table_shape": True,
    "columns": [
        ["root_device", "numeric", -1, True, True, True],
        ["root_inode", "numeric", -1, True, True, True],
    ],
    "functions": [
        [
            "public_receipt_witness_append",
            "e480369597ef04265e650ae4a1098869c431c028968ad14e3d91d2d435f4cdc0",
            True,
            False,
            "jsonb",
            "v",
            True,
            True,
            True,
            True,
        ],
        [
            "public_receipt_witness_immutable",
            "bbcafd05fca41bf9269dee5a187344a400b718fa301ff48b6757b996487ae01f",
            False,
            False,
            "",
            "v",
            True,
            True,
            True,
            True,
        ],
        [
            "public_receipt_witness_insert_guard",
            "74cf98c2c28cfee8cd63d24213957876a602373f65c948f2b4f83b7e466c4e81",
            False,
            False,
            "",
            "v",
            True,
            True,
            True,
            True,
        ],
        [
            "public_receipt_witness_read",
            "f46ee8b9ccc9e3594d12a7490b9fd68c9dc7d6dfd905f3a864bd490c07d9aefb",
            True,
            True,
            "text",
            "s",
            True,
            True,
            True,
            True,
        ],
    ],
    "triggers": [
        [
            "public_receipt_witness_immutable",
            "O",
            27,
            "public_receipt_witness_immutable",
            True,
        ],
        [
            "public_receipt_witness_insert_guard",
            "O",
            7,
            "public_receipt_witness_insert_guard",
            True,
        ],
        [
            "public_receipt_witness_no_truncate",
            "O",
            34,
            "public_receipt_witness_immutable",
            True,
        ],
    ],
    "constraint": [
        "ck_public_receipt_witness_revisions_native_identity_bounds",
        "c",
        True,
        False,
        False,
        None,  # PENDING_NATIVE_DB0047_CONSTRAINT_BASELINE: fail closed.
    ],
}
