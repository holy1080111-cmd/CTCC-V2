"""Source-pinned PG17 btree admission using catalog rows only.

No application row, index, type output, operator, support function or AM
handler is invoked. The bootstrap facts were parsed from the seven exact
REL_17_11 primary catalog files whose SHA256 identities are retained in the
fixed source artifact. This is physical read safety, never schema acceptance.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

_FACTS_SHA256 = "54a2bd07dc7d905ff3844c0f49b9035daa2997a5271c8a6670f57f53a2d3de41"


def _source_facts():
    path = Path(__file__).with_name("native_owner_pg17_btree_facts_v1.json")
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or getattr(before, "st_file_attributes", 0) & 0x400
    ):
        raise ValueError("native_owner_btree_source_refused") from None
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        raw = os.read(descriptor, 100001)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    def stable_identity(info):
        # Reading may legitimately update atime. Security/identity fields and
        # the exact source bytes must remain unchanged across both reads.
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            getattr(info, "st_file_attributes", 0),
        )

    if (
        stable_identity(before) != stable_identity(opened)
        or stable_identity(opened) != stable_identity(after)
        or opened.st_ctime_ns != after.st_ctime_ns
        or not 0 < len(raw) <= 100000
        or hashlib.sha256(raw).hexdigest() != _FACTS_SHA256
    ):
        raise ValueError("native_owner_btree_source_refused") from None
    return json.loads(raw)


def _literal(value):
    # Values come only from the source artifact pinned above. This function is
    # not a SQL API and does not consume observed/caller catalog data.
    return (
        "'"
        + json.dumps(value, separators=(",", ":")).replace("'", "''")
        + "'::pg_catalog.jsonb"
    )


def _integer_set(values):
    if not values or any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("native_owner_btree_source_refused") from None
    return ",".join(str(value) for value in sorted(set(values)))


def _build_source_predicate():
    facts = _source_facts()
    family_ids = _integer_set([row[0] for row in facts["families"]])
    procedure_ids = _integer_set([row[0] for row in facts["procedures"]])
    operator_ids = _integer_set([row[0] for row in facts["operators"]])
    # pg_opclass OIDs are allocated by initdb. Its exact source name, namespace,
    # bootstrap owner/range, method, family/input/key type and default property
    # are checked instead; allocation is not treated as source identity.
    classes = _literal(facts["classes"])
    families = _literal(facts["families"])
    procedures = _literal(facts["procedures"])
    operators = _literal(facts["operators"])
    amproc = _literal(facts["amproc"])
    amop = _literal(facts["amop"])
    types = _literal(facts["types"])
    constant_type_ids = _integer_set([row[0] for row in facts["types"]])
    # All these pg_catalog columns have fixed native types. No deparser or
    # observed function is called while constructing the comparison rows.
    return f"""(
 NOT EXISTS(SELECT 1 FROM pg_catalog.pg_event_trigger)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_am a WHERE a.oid=403 AND
  (a.amname<>'btree' OR a.amtype<>'i' OR a.amhandler<>330))
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_am a WHERE a.oid=403)
 AND NOT EXISTS(
 SELECT 1 FROM (
 SELECT d.adbin::pg_catalog.text raw FROM pg_catalog.pg_attrdef d
 JOIN pg_catalog.pg_class c ON c.oid=d.adrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
 UNION ALL SELECT c.conbin::pg_catalog.text FROM pg_catalog.pg_constraint c
 JOIN pg_catalog.pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public' AND c.conbin IS NOT NULL
 UNION ALL SELECT t.tgqual::pg_catalog.text FROM pg_catalog.pg_trigger t
 JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND t.tgqual IS NOT NULL
 UNION ALL SELECT p.proargdefaults::pg_catalog.text FROM pg_catalog.pg_proc p
 JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proargdefaults IS NOT NULL
 UNION ALL SELECT p.prosqlbody::pg_catalog.text FROM pg_catalog.pg_proc p
 JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prosqlbody IS NOT NULL
 ) expression
 CROSS JOIN LATERAL pg_catalog.regexp_matches(expression.raw,'[:]consttype ([0-9]+)','g') m(parts)
 WHERE m.parts[1]::pg_catalog.int8 NOT IN ({constant_type_ids}))
 AND NOT EXISTS(
 SELECT 1 FROM pg_catalog.jsonb_array_elements({types}) expected(value)
 LEFT JOIN pg_catalog.pg_type t ON t.oid=(expected.value->>0)::pg_catalog.oid
 WHERE t.oid IS NULL OR t.typnamespace<>11 OR t.typowner<>10 OR t.typtype<>'b'
 OR NOT t.typisdefined OR t.typrelid<>0 OR t.typbasetype<>0 OR t.typnotnull
 OR t.typdefault IS NOT NULL OR t.typdefaultbin IS NOT NULL
 OR pg_catalog.jsonb_build_array(t.oid::pg_catalog.int8,t.typname::pg_catalog.text,t.typelem::pg_catalog.int8,
 t.typinput::pg_catalog.oid::pg_catalog.int8,t.typoutput::pg_catalog.oid::pg_catalog.int8,
 t.typreceive::pg_catalog.oid::pg_catalog.int8,t.typsend::pg_catalog.oid::pg_catalog.int8,
 t.typmodin::pg_catalog.oid::pg_catalog.int8,t.typmodout::pg_catalog.oid::pg_catalog.int8,
 t.typanalyze::pg_catalog.oid::pg_catalog.int8,t.typsubscript::pg_catalog.oid::pg_catalog.int8)
 IS DISTINCT FROM expected.value)
 AND NOT EXISTS(
 SELECT 1 FROM pg_catalog.jsonb_array_elements({procedures}) expected(value)
 LEFT JOIN pg_catalog.pg_proc p ON p.oid=(expected.value->>0)::pg_catalog.oid
 WHERE p.oid IS NULL OR p.pronamespace<>11 OR p.proowner<>10 OR p.prolang<>12
 OR p.prokind<>'f' OR p.proconfig IS NOT NULL OR p.probin IS NOT NULL
 OR p.proallargtypes IS NOT NULL OR p.proargmodes IS NOT NULL
 OR p.proargdefaults IS NOT NULL OR p.protrftypes IS NOT NULL
 OR p.prosqlbody IS NOT NULL OR p.proacl IS NOT NULL
 OR pg_catalog.jsonb_build_array(p.oid::pg_catalog.int8,p.proname::pg_catalog.text,
 p.proargtypes::pg_catalog.text,p.prorettype::pg_catalog.int8,p.prosrc,
 p.provolatile::pg_catalog.text,p.proparallel::pg_catalog.text,p.proisstrict,
 p.proleakproof,p.prosecdef,p.proretset,p.pronargdefaults,
 p.provariadic::pg_catalog.int8,p.prosupport::pg_catalog.int8,
 p.procost::pg_catalog.text,p.prorows::pg_catalog.text,p.proargnames) IS DISTINCT FROM expected.value)
 AND NOT EXISTS(
 SELECT 1 FROM pg_catalog.jsonb_array_elements({operators}) expected(value)
 LEFT JOIN pg_catalog.pg_operator o ON o.oid=(expected.value->>0)::pg_catalog.oid
 WHERE o.oid IS NULL OR o.oprnamespace<>11 OR o.oprowner<>10
 OR pg_catalog.jsonb_build_array(o.oid::pg_catalog.int8,o.oprname::pg_catalog.text,
 o.oprleft::pg_catalog.int8,o.oprright::pg_catalog.int8,o.oprresult::pg_catalog.int8,
 o.oprcode::pg_catalog.oid::pg_catalog.int8,o.oprrest::pg_catalog.oid::pg_catalog.int8,
 o.oprjoin::pg_catalog.oid::pg_catalog.int8,o.oprcom::pg_catalog.int8,o.oprnegate::pg_catalog.int8,
 o.oprcanmerge,o.oprcanhash,o.oprkind::pg_catalog.text) IS DISTINCT FROM expected.value)
 AND NOT EXISTS(
 SELECT 1 FROM pg_catalog.jsonb_array_elements({families}) expected(value)
 LEFT JOIN pg_catalog.pg_opfamily f ON f.oid=(expected.value->>0)::pg_catalog.oid
 WHERE f.oid IS NULL OR f.opfnamespace<>11 OR f.opfowner<>10 OR f.opfmethod<>403
 OR pg_catalog.jsonb_build_array(f.oid::pg_catalog.int8,f.opfname::pg_catalog.text) IS DISTINCT FROM expected.value)
 AND (SELECT COALESCE(pg_catalog.jsonb_agg(actual.value ORDER BY actual.value),'[]'::pg_catalog.jsonb)
 FROM (SELECT pg_catalog.jsonb_build_array(p.amprocfamily::pg_catalog.int8,p.amproclefttype::pg_catalog.int8,
 p.amprocrighttype::pg_catalog.int8,p.amprocnum,p.amproc::pg_catalog.oid::pg_catalog.int8) value
 FROM pg_catalog.pg_amproc p WHERE p.amprocfamily IN ({family_ids})) actual)
 = (SELECT pg_catalog.jsonb_agg(value ORDER BY value) FROM pg_catalog.jsonb_array_elements({amproc}))
 AND (SELECT COALESCE(pg_catalog.jsonb_agg(actual.value ORDER BY actual.value),'[]'::pg_catalog.jsonb)
 FROM (SELECT pg_catalog.jsonb_build_array(o.amopfamily::pg_catalog.int8,o.amoplefttype::pg_catalog.int8,
 o.amoprighttype::pg_catalog.int8,o.amopstrategy,o.amoppurpose::pg_catalog.text,
 o.amopopr::pg_catalog.int8,o.amopmethod::pg_catalog.int8,o.amopsortfamily::pg_catalog.int8) value
 FROM pg_catalog.pg_amop o WHERE o.amopfamily IN ({family_ids})) actual)
 = (SELECT pg_catalog.jsonb_agg(value ORDER BY value) FROM pg_catalog.jsonb_array_elements({amop}))
 AND NOT EXISTS(
 SELECT 1 FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class ix ON ix.oid=i.indexrelid
 JOIN pg_catalog.pg_class t ON t.oid=i.indrelid
 JOIN pg_catalog.pg_namespace tn ON tn.oid=t.relnamespace
 WHERE (tn.nspname='public' OR EXISTS(SELECT 1 FROM pg_catalog.pg_class parent
 JOIN pg_catalog.pg_namespace pn ON pn.oid=parent.relnamespace
 WHERE pn.nspname='public' AND parent.reltoastrelid=t.oid)) AND (
 ix.relkind<>'i' OR ix.relam<>403 OR ix.relpersistence<>'p' OR ix.relispartition
 OR NOT i.indisvalid OR NOT i.indisready OR NOT i.indislive
 OR i.indnkeyatts<>i.indnatts OR i.indexprs IS NOT NULL
 OR EXISTS(SELECT 1 FROM pg_catalog.generate_series(0,i.indnatts-1) keyno(n)
 LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=t.oid AND a.attnum=i.indkey[keyno.n]
 LEFT JOIN pg_catalog.pg_opclass c ON c.oid=i.indclass[keyno.n]
 WHERE a.attnum IS NULL OR a.attnum<=0 OR a.attisdropped
 OR c.oid IS NULL OR c.oid>=16384 OR c.opcnamespace<>11 OR c.opcowner<>10
 OR c.opcmethod<>403 OR NOT c.opcdefault OR c.opcfamily NOT IN ({family_ids})
 OR NOT EXISTS(SELECT 1 FROM pg_catalog.jsonb_array_elements({classes}) expected(value)
 WHERE expected.value=pg_catalog.jsonb_build_array(c.opcname::pg_catalog.text,
 c.opcfamily::pg_catalog.int8,c.opcintype::pg_catalog.int8,c.opckeytype::pg_catalog.int8,c.opcdefault))
 OR NOT (a.atttypid=c.opcintype OR (a.atttypid=1043 AND c.opcintype=25))
 OR i.indcollation[keyno.n] NOT IN (0,100) OR i.indoption[keyno.n] NOT IN (0,1,2,3))
 OR EXISTS(SELECT 1 FROM pg_catalog.pg_depend d WHERE d.classid='pg_catalog.pg_class'::pg_catalog.regclass
 AND d.objid=i.indexrelid AND (d.objsubid<>0 OR d.deptype NOT IN ('n','a','i') OR NOT (
 (d.refclassid='pg_catalog.pg_class'::pg_catalog.regclass AND d.refobjid=t.oid AND d.refobjsubid>=0
 AND (d.refobjsubid=0 OR EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a
 WHERE a.attrelid=t.oid AND a.attnum=d.refobjsubid AND NOT a.attisdropped)))
 OR (d.refclassid='pg_catalog.pg_opclass'::pg_catalog.regclass AND d.refobjsubid=0
 AND d.refobjid=ANY(i.indclass::pg_catalog.oid[]))
 OR (d.refclassid='pg_catalog.pg_collation'::pg_catalog.regclass AND d.refobjsubid=0 AND d.refobjid=100)
 OR (d.refclassid='pg_catalog.pg_constraint'::pg_catalog.regclass AND d.refobjsubid=0
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_constraint c WHERE c.oid=d.refobjid AND c.conindid=i.indexrelid
 AND c.conrelid=t.oid AND c.contype IN ('p','u','x')))
 OR (d.refclassid='pg_catalog.pg_namespace'::pg_catalog.regclass AND d.refobjsubid=0
 AND d.refobjid IN (11,t.relnamespace))
 OR (d.refclassid='pg_catalog.pg_operator'::pg_catalog.regclass AND d.refobjsubid=0 AND d.refobjid IN ({operator_ids}))
 OR (d.refclassid='pg_catalog.pg_proc'::pg_catalog.regclass AND d.refobjsubid=0 AND d.refobjid IN ({procedure_ids}))
 OR (d.refclassid='pg_catalog.pg_type'::pg_catalog.regclass AND d.refobjsubid=0
 AND d.refobjid IN (16,17,20,21,23,25,26,114,700,701,869,1042,1043,1082,1114,1184,1700,2950,3802,1009,1015)))))
 OR (i.indpred IS NOT NULL AND (
 EXISTS(SELECT 1 FROM pg_catalog.regexp_matches(i.indpred::pg_catalog.text,'\\{{([A-Z][A-Z0-9_]*)','g') m(parts)
 WHERE m.parts[1] NOT IN ('VAR','CONST','OPEXPR','BOOLEXPR','RELABELTYPE','SCALARARRAYOPEXPR','ARRAYEXPR','COERCEVIAIO','NULLTEST','BOOLEANTEST'))
 OR EXISTS(SELECT 1 FROM pg_catalog.regexp_matches(i.indpred::pg_catalog.text,
 ':(vartype|consttype|opresulttype|resulttype|array_typeid|element_typeid) ([0-9]+)','g') m(parts)
 WHERE m.parts[2]::pg_catalog.int8 NOT IN (16,17,20,21,23,25,26,114,700,701,869,1042,1043,1082,1114,1184,1700,2950,3802,1009,1015))
 OR EXISTS(SELECT 1 FROM pg_catalog.regexp_matches(i.indpred::pg_catalog.text,'[:]opno ([0-9]+)','g') m(parts)
 WHERE m.parts[1]::pg_catalog.int8 NOT IN (98,531,3477))
 OR EXISTS(SELECT 1 FROM pg_catalog.regexp_matches(i.indpred::pg_catalog.text,'[:]opfuncid ([0-9]+)','g') m(parts)
 WHERE m.parts[1]::pg_catalog.int8 NOT IN (0,67,157,3214))
 OR EXISTS(SELECT 1 FROM pg_catalog.regexp_matches(i.indpred::pg_catalog.text,
 ':(varcollid|constcollid|opcollid|inputcollid|resultcollid|array_collid) ([0-9]+)','g') m(parts)
 WHERE m.parts[2]::pg_catalog.int8 NOT IN (0,100))
 OR i.indpred::pg_catalog.text ~ ':(hashfuncid|negfuncid|varlevelsup) [1-9]'
 )))
 AND EXISTS(SELECT 1 FROM pg_catalog.pg_collation c WHERE c.oid=100 AND c.collnamespace=11
 AND c.collowner=10 AND c.collname='default' AND c.collprovider='d' AND c.collisdeterministic
 AND c.collencoding=-1 AND c.collcollate IS NULL AND c.collctype IS NULL
 AND c.colllocale IS NULL AND c.collicurules IS NULL AND c.collversion IS NULL)
)"""


NATIVE_BTREE_CATALOG_PREDICATE_V1 = _build_source_predicate()
