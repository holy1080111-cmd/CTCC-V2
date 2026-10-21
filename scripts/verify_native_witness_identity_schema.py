"""DB0047 catalog comparison pins, including the reviewed PG17 constraint.

The constraint deparser digest comes from exact-source diagnostic 39690256720.
It is a comparison input: every field in the full catalog query must still be
read and matched. These pins alone are not deployed/native schema acceptance.
"""

import re
from copy import deepcopy


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
# deparser hash is the reviewed PG17.11 pretty=true result from run39690256720,
# exact source 1fbbf170a962d46c3851e06b8f05f5aa129a4043. The integral u64/u128
# bounds and unbounded NUMERIC shape were reviewed independently; all remaining
# catalog/ownership/ACL/trigger predicates below remain mandatory.
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
        "b19f38740a3756948a3c17f97c4d4433d84010a7311a81a9bc3567eab1e5534b",
    ],
}


def expected_legacy_witness_identity_schema() -> dict:
    """The DB0024 contract, separately checked after DB0047's empty downgrade."""
    result = deepcopy(NATIVE_WITNESS_IDENTITY_SCHEMA)
    result["columns"] = [
        ["root_device", "bigint", -1, True, True, True],
        ["root_inode", "bigint", -1, True, True, True],
    ]
    result["constraint"] = []
    legacy_bodies = {
        "public_receipt_witness_append": "32acf9da9cc4888b51737409f85e0d5411f96deba83dfe397fa7d8db5dcd82c8",
        "public_receipt_witness_insert_guard": "60998175f712cafb5b7525691c060c42455ecf2658404a0fed22864b821f3d1f",
    }
    for row in result["functions"]:
        if row[0] in legacy_bodies:
            row[1] = legacy_bodies[row[0]]
    return result


# Attributes which the successful type/body change must preserve. Type OIDs,
# changed bodies and constraints are deliberately validated separately rather
# than compared for equality across the expected ALTER TYPE/guard replacement.
NATIVE_WITNESS_OBJECT_CUSTODY_SQL = """
SELECT encode(sha256(convert_to(jsonb_build_object(
 'table',(SELECT jsonb_build_array(oid,relowner,reltype,relacl::text)
   FROM pg_catalog.pg_class WHERE oid=
     pg_catalog.to_regclass('public.public_receipt_witness_revisions')),
 'columns',(SELECT jsonb_agg(jsonb_build_array(attnum,attname,attnotnull,
   attidentity::text,attgenerated::text) ORDER BY attnum)
   FROM pg_catalog.pg_attribute WHERE attrelid=
     pg_catalog.to_regclass('public.public_receipt_witness_revisions')
     AND attnum>0 AND NOT attisdropped),
 'functions',(SELECT jsonb_agg(jsonb_build_array(oid,proname,proowner,
   proacl::text,prosecdef,proconfig,prorettype,proretset,proargtypes::text,
   provolatile::text,proisstrict,proparallel::text,proleakproof,prokind::text,
   prolang,prosupport) ORDER BY oid) FROM pg_catalog.pg_proc
   WHERE pronamespace='public'::pg_catalog.regnamespace AND proname IN
     ('public_receipt_witness_append','public_receipt_witness_insert_guard',
      'public_receipt_witness_immutable','public_receipt_witness_read')),
 'triggers',(SELECT jsonb_agg(jsonb_build_array(oid,
   pg_catalog.pg_get_triggerdef(oid,true),tgenabled::text,tgfoid) ORDER BY oid)
   FROM pg_catalog.pg_trigger WHERE tgrelid=
     pg_catalog.to_regclass('public.public_receipt_witness_revisions'))
)::text,'UTF8')),'hex')
"""


# This bounded read returns only counts and digests. The digest includes all
# stored columns, not just the self-reported record hash. Catalog custody covers
# actual OIDs, ACLs, bodies, trigger definitions and constraints across refusal.
NATIVE_WITNESS_RETENTION_SNAPSHOT_SQL = """
WITH bounded AS MATERIALIZED (
 SELECT * FROM public.public_receipt_witness_revisions
 ORDER BY journal_key,revision LIMIT 100001
), row_state AS (
 SELECT count(*) AS row_count,
 count(*) FILTER(WHERE root_device<=9223372036854775807
   AND root_inode<=9223372036854775807) AS small_row_count,
 count(*) FILTER(WHERE root_device>9223372036854775807
   OR root_inode>9223372036854775807) AS wide_row_count,
 encode(sha256(convert_to(COALESCE(string_agg(
   encode(sha256(convert_to(row_to_json(b)::text,'UTF8')),'hex'),''
   ORDER BY journal_key,revision),''),'UTF8')),'hex') AS records_sha256
 FROM bounded b
), catalog_state AS (
 SELECT jsonb_build_object(
 'table',(SELECT jsonb_build_array(oid,relowner,relacl::text)
   FROM pg_catalog.pg_class WHERE oid=
     'public.public_receipt_witness_revisions'::pg_catalog.regclass),
 'columns',(SELECT jsonb_agg(jsonb_build_array(attnum,attname,atttypid,
   atttypmod,attnotnull,attidentity::text,attgenerated::text) ORDER BY attnum)
   FROM pg_catalog.pg_attribute WHERE attrelid=
     'public.public_receipt_witness_revisions'::pg_catalog.regclass
     AND attnum>0 AND NOT attisdropped),
 'functions',(SELECT jsonb_agg(jsonb_build_array(oid,proname,proowner,
   proacl::text,prosecdef,proconfig,prorettype,proretset,proargtypes::text,
   provolatile::text,proisstrict,proparallel::text,proleakproof,prokind::text,
   prolang,prosupport,prosrc) ORDER BY oid) FROM pg_catalog.pg_proc
   WHERE pronamespace='public'::pg_catalog.regnamespace AND proname IN
     ('public_receipt_witness_append','public_receipt_witness_insert_guard',
      'public_receipt_witness_immutable','public_receipt_witness_read')),
 'triggers',(SELECT jsonb_agg(jsonb_build_array(oid,
   pg_catalog.pg_get_triggerdef(oid,true),tgenabled::text,tgfoid) ORDER BY oid)
   FROM pg_catalog.pg_trigger WHERE tgrelid=
     'public.public_receipt_witness_revisions'::pg_catalog.regclass),
 'constraints',(SELECT jsonb_agg(jsonb_build_array(oid,
   pg_catalog.pg_get_constraintdef(oid,true),convalidated) ORDER BY oid)
   FROM pg_catalog.pg_constraint WHERE conrelid=
     'public.public_receipt_witness_revisions'::pg_catalog.regclass)
 ) AS state
)
SELECT jsonb_build_object('row_count',row_count,'small_row_count',small_row_count,
 'wide_row_count',wide_row_count,'records_sha256',records_sha256,
 'catalog_sha256',encode(sha256(convert_to(state::text,'UTF8')),'hex'))
FROM row_state CROSS JOIN catalog_state
"""
