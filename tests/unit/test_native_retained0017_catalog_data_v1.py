"""Independent synthetic catalog DATA differentials; never schema evidence.

The deliberately small rows are not a PostgreSQL catalog and never enter a
collector, a literal seal or maintenance admission. The two genuine fresh PG
reference cases remain the positive integration proof.
"""

import copy

import pytest

from scripts import retained0017_catalog_baseline_v1 as subject

FUNCTIONS = (
    "qualification_ledger_immutable",
    "qualification_reservation_update",
    "qualification_scope_update",
)
PREFIX = "a" * 64


def _constant(value, *, oid=16, location=-1):
    length = 1 if oid == 16 else 4
    payload = value.to_bytes(8, "little")
    signed = " ".join(str(byte if byte < 128 else byte - 256) for byte in payload)
    return (
        "{CONST :consttype "
        + str(oid)
        + " :consttypmod -1 :constcollid 0 :constlen "
        + str(length)
        + " :constbyval true :constisnull false :location "
        + str(location)
        + " :constvalue "
        + str(length)
        + " [ "
        + signed
        + " ]}"
    )


def _sequence(oid, *, location=-1):
    return (
        "{FUNCEXPR :funcid 1574 :funcresulttype 20 :funcretset false :funcvariadic false"
        " :funcformat 0 :funccollid 0 :inputcollid 0 :args ("
        + _constant(oid, oid=2205, location=location)
        + ") :location "
        + str(location)
        + "}"
    )


def _row(document, kind, *, name=None):
    matches = [
        row
        for actual, row, _extra in document["entries"]
        if actual == kind
        and (name is None or row.get("proname", row.get("relname")) == name)
    ]
    assert len(matches) == 1
    return matches[0]


def _document(label, base):
    owner, database = "declared_owner_" + label, "declared_db_" + label
    table, sequence, index, constraint, default = (
        base + value for value in range(1, 6)
    )
    owner_oid, database_oid, composite = base + 10, base + 11, base + 12
    language, call, inline, validator, extension, default_acl = (
        base + value for value in range(20, 26)
    )
    references = {}

    def add(catalog, oid, identity, *, schema=None, name=None, kind=None):
        references[catalog, oid] = {
            "type": kind or catalog,
            "schema": schema,
            "name": name,
            "identity": identity,
        }

    for oid, name in ((11, "pg_catalog"), (2200, "public")):
        add("pg_namespace", oid, name, kind="schema")
    add("pg_authid", 10, "declared_bootstrap_owner", kind="role")
    add("pg_authid", 6171, "pg_database_owner", kind="role")
    add("pg_authid", owner_oid, owner, kind="role")
    add("pg_database", database_oid, database, kind="database")
    add("pg_am", 2, "heap", kind="table access method")
    add("pg_am", 403, "btree", kind="index access method")
    add("pg_tablespace", 1663, "pg_default", kind="tablespace")
    for oid, name, kind in (
        (table, "declared_fixture", "table"),
        (sequence, "declared_fixture_id_seq", "sequence"),
        (index, "declared_fixture_pkey", "index"),
    ):
        add("pg_class", oid, "public." + name, schema="public", name=name, kind=kind)
    add(
        "pg_constraint",
        constraint,
        "declared_fixture_pkey on public.declared_fixture",
        schema="public",
        kind="table constraint",
    )
    add(
        "pg_attrdef",
        default,
        "for public.declared_fixture.id",
        schema="public",
        kind="default value",
    )
    add(
        "pg_default_acl",
        default_acl,
        "for role " + owner + " in schema public on tables",
        kind="default ACL",
    )
    for oid, name in ((12, "internal"), (13, "c"), (14, "sql"), (language, "plpgsql")):
        add("pg_language", oid, name, kind="language")
    for oid, name in (
        (16, "boolean"),
        (20, "bigint"),
        (23, "integer"),
        (26, "oid"),
        (2205, "regclass"),
        (2278, "void"),
        (2279, "trigger"),
        (2281, "internal"),
        (3115, "language_handler"),
        (composite, "public.declared_fixture"),
    ):
        add(
            "pg_type",
            oid,
            name,
            schema="public" if oid == composite else "pg_catalog",
            kind="type",
        )
    add(
        "pg_opclass",
        10001,
        "pg_catalog.int8_ops USING btree",
        schema="pg_catalog",
        kind="operator class",
    )
    for oid, name in ((100, "default"), (950, "C")):
        add(
            "pg_collation",
            oid,
            "pg_catalog." + name,
            schema="pg_catalog",
            name=name,
            kind="collation",
        )

    callbacks = []
    callback_specs = (
        (call, "plpgsql_call_handler", [], 3115, 13),
        (inline, "plpgsql_inline_handler", [2281], 2278, 13),
        (validator, "plpgsql_validator", [26], 2278, 13),
        (2246, "fmgr_internal_validator", [26], 2278, 12),
        (2247, "fmgr_c_validator", [26], 2278, 12),
        (2248, "fmgr_sql_validator", [26], 2278, 12),
    )
    for oid, name, arguments, returns, lang in callback_specs:
        add(
            "pg_proc",
            oid,
            "pg_catalog." + name + "()",
            schema="pg_catalog",
            name=name,
            kind="function",
        )
        callbacks.append(
            {
                "oid": oid,
                "proname": name,
                "proowner": 10,
                "pronamespace": 11,
                "prolang": lang,
                "provariadic": 0,
                "prosupport": 0,
                "prorettype": returns,
                "proargtypes": arguments,
                "proallargtypes": None,
                "protrftypes": None,
                "proacl": None,
                "prosrc": name,
                "prokind": "f",
                "proargdefaults": None,
                "prosqlbody": None,
            }
        )
    languages = [
        {
            "oid": oid,
            "lanname": name,
            "lanowner": 10,
            "lanacl": None,
            "lanplcallfoid": call_oid,
            "laninline": inline_oid,
            "lanvalidator": validator_oid,
            "lanpltrusted": name in {"sql", "plpgsql"},
        }
        for oid, name, call_oid, inline_oid, validator_oid in (
            (12, "internal", 0, 0, 2246),
            (13, "c", 0, 0, 2247),
            (14, "sql", 0, 0, 2248),
            (language, "plpgsql", call, inline, validator),
        )
    ]
    entries = []
    for oid, name, kind, am, type_oid in (
        (table, "declared_fixture", "r", 2, composite),
        (sequence, "declared_fixture_id_seq", "S", 0, 0),
        (index, "declared_fixture_pkey", "i", 403, 0),
    ):
        entries.append(
            [
                "relation",
                {
                    "oid": oid,
                    "relname": name,
                    "relkind": kind,
                    "relnamespace": 2200,
                    "reltype": type_oid,
                    "relowner": owner_oid,
                    "relam": am,
                    "reltablespace": 0,
                    "reltoastrelid": 0,
                    "relrewrite": 0,
                    "relispartition": False,
                    "relacl": None,
                    "relfilenode": oid + 100,
                    "relpages": base,
                    "reltuples": float(base),
                    "relallvisible": 0,
                    "relfrozenxid": base + 200,
                    "relminmxid": base + 201,
                },
                {},
            ]
        )
    entries.extend(
        [
            [
                "attribute",
                {
                    "attrelid": table,
                    "attnum": 1,
                    "attname": "id",
                    "atttypid": 20,
                    "attcollation": 0,
                    "attacl": None,
                    "attisdropped": False,
                    "attnotnull": True,
                    "atttypmod": -1,
                },
                {},
            ],
            [
                "default",
                {
                    "oid": default,
                    "adrelid": table,
                    "adnum": 1,
                    "adbin": _sequence(sequence, location=base),
                },
                {},
            ],
            [
                "constraint",
                {
                    "oid": constraint,
                    "conname": "declared_fixture_pkey",
                    "connamespace": 2200,
                    "conrelid": table,
                    "contypid": 0,
                    "conindid": index,
                    "confrelid": 0,
                    "conparentid": 0,
                    "conbin": _constant(1, location=base),
                    "conpfeqop": None,
                    "conppeqop": None,
                    "conffeqop": None,
                    "conexclop": None,
                    "contype": "p",
                    "conkey": [1],
                },
                {},
            ],
            [
                "index",
                {
                    "indexrelid": index,
                    "indrelid": table,
                    "indexprs": None,
                    "indpred": None,
                    "indclass": [10001],
                    "indcollation": [0],
                    "indkey": [1],
                    "indisunique": True,
                },
                {},
            ],
            [
                "sequence",
                {
                    "seqrelid": sequence,
                    "seqtypid": 20,
                    "seqstart": 1,
                    "seqincrement": 1,
                    "seqmax": 2**63 - 1,
                    "seqmin": 1,
                    "seqcache": 1,
                    "seqcycle": False,
                },
                {},
            ],
            [
                "type",
                {
                    "oid": composite,
                    "typname": "declared_fixture",
                    "typnamespace": 2200,
                    "typowner": owner_oid,
                    "typrelid": table,
                    "typelem": 0,
                    "typarray": 0,
                    "typbasetype": 0,
                    "typcollation": 0,
                    "typsubscript": 0,
                    "typinput": 0,
                    "typoutput": 0,
                    "typreceive": 0,
                    "typsend": 0,
                    "typmodin": 0,
                    "typmodout": 0,
                    "typanalyze": 0,
                    "typtype": "c",
                    "typlen": -1,
                    "typdefaultbin": None,
                    "typacl": None,
                },
                {},
            ],
            [
                "namespace",
                {
                    "oid": 2200,
                    "nspname": "public",
                    "nspowner": 6171,
                    "nspacl": [
                        "pg_database_owner=UC/pg_database_owner",
                        "=U/pg_database_owner",
                    ],
                },
                {},
            ],
            [
                "database",
                {
                    "oid": database_oid,
                    "datname": database,
                    "datdba": owner_oid,
                    "dattablespace": 1663,
                    "datacl": None,
                    "encoding": 6,
                    "datfrozenxid": base,
                    "datminmxid": base + 1,
                },
                {},
            ],
            [
                "default_acl",
                {
                    "oid": default_acl,
                    "defaclrole": owner_oid,
                    "defaclnamespace": 2200,
                    "defaclobjtype": "r",
                    "defaclacl": [owner + "=arwdDxt/" + owner],
                },
                {},
            ],
        ]
    )
    for offset, name in enumerate(FUNCTIONS, 30):
        oid = base + offset
        add(
            "pg_proc",
            oid,
            "public." + name + "()",
            schema="public",
            name=name,
            kind="function",
        )
        entries.append(
            [
                "function",
                {
                    "oid": oid,
                    "proname": name,
                    "pronamespace": 2200,
                    "proowner": owner_oid,
                    "prolang": language,
                    "provariadic": 0,
                    "prosupport": 0,
                    "prorettype": 2279,
                    "proargtypes": [],
                    "proallargtypes": None,
                    "protrftypes": None,
                    "proacl": None,
                    "prokind": "f",
                    "proargdefaults": None,
                    "prosrc": "BEGIN RETURN NEW; END;",
                },
                {},
            ]
        )
    return {
        "schema": "ctcc.retained0017.raw_catalog.v1",
        "profile": {
            "owner": owner,
            "session_owner": owner,
            "role": "none",
            "database": database,
            "read_only": "on",
            "search_path": "pg_catalog",
            "timezone": "UTC",
            "server_version": "170011",
            "tls": True,
            "datum_abi": "pg17.amd64.little.datum8.signedchar",
        },
        "head": ["0017"],
        "references": [
            [catalog, oid, address]
            for (catalog, oid), address in sorted(references.items())
        ],
        "toast": [],
        "entries": entries,
        "dependencies": [
            ["pg_attrdef", default, 0, "pg_class", sequence, 0, "n"],
            ["pg_attrdef", default, 0, "pg_class", table, 1, "a"],
            ["pg_class", sequence, 0, "pg_class", table, 1, "a"],
        ],
        "shared_dependencies": [
            [database_oid, "pg_class", table, 0, "pg_authid", owner_oid, "o"],
            [0, "pg_database", database_oid, 0, "pg_authid", owner_oid, "o"],
        ],
        "bootstrap": {
            "languages": languages,
            "language_callbacks": callbacks,
            "expression_callbacks": [],
            "expression_operators": [],
            "extension": {
                "oid": extension,
                "extname": "plpgsql",
                "extowner": 10,
                "extnamespace": 11,
                "extversion": "1.0",
                "extrelocatable": False,
                "extconfig": None,
                "extcondition": None,
            },
            "collations": [
                {
                    "oid": oid,
                    "collname": name,
                    "collnamespace": 11,
                    "collowner": 10,
                    "collprovider": provider,
                    "collisdeterministic": True,
                    "collencoding": -1,
                    "collversion": None,
                }
                for oid, name, provider in ((100, "default", "d"), (950, "C", "c"))
            ],
        },
        "actor_physical": {
            "database_oid": database_oid,
            "owner": [owner_oid, owner, False, False, False, False, True, False, False],
            "membership_count": 0,
            "membership_sha256": "b" * 64,
            "maintenance_settings_sha256": "c" * 64,
        },
    }


@pytest.fixture
def data_pair(monkeypatch):
    monkeypatch.setattr(
        subject,
        "source17_contract_v1",
        lambda: {
            "columns": {"declared_fixture": ["id"]},
            "source17_prefix_sha256": PREFIX,
        },
    )
    return _document("a", 20000), _document("b", 40000)


def test_distinct_physical_allocations_and_actor_names_normalize_only_structural_data(
    data_pair,
):
    first, second = data_pair
    before = subject.canonical(first), subject.canonical(second)
    assert first["actor_physical"]["owner"][0] != second["actor_physical"]["owner"][0]
    assert (
        first["actor_physical"]["database_oid"]
        != second["actor_physical"]["database_oid"]
    )
    assert subject.normalize_catalog17_v1(first) == subject.normalize_catalog17_v1(
        second
    )
    assert before == (subject.canonical(first), subject.canonical(second))
    assert subject.REFERENCE_STATUS == "PENDING_TWO_INDEPENDENT_ACTUAL_PG17_REFERENCES"
    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.require_sealed_catalog17_v1(first)


def test_bootstrap_language_acl_stays_separate_from_application_owner_acl(data_pair):
    normalized = []
    for index, document in enumerate(copy.deepcopy(data_pair)):
        bootstrap_owner = "declared_bootstrap_owner_" + str(index)
        for catalog, oid, address in document["references"]:
            if catalog == "pg_authid" and oid == 10:
                address["identity"] = bootstrap_owner
        language = next(
            row
            for row in document["bootstrap"]["languages"]
            if row["lanname"] == "plpgsql"
        )
        language["lanacl"] = [
            bootstrap_owner + "=U/" + bootstrap_owner,
            "=U/" + bootstrap_owner,
        ]
        normalized.append(subject.normalize_catalog17_v1(document))
    assert normalized[0] == normalized[1]
    assert b"$PG_BOOTSTRAP_OWNER_10" in subject.canonical(normalized[0])
    assert b"pg_catalog.pg_database_owner" in subject.canonical(normalized[0])


@pytest.mark.parametrize(
    "grantor", ["declared_bootstrap_owner", "declared_unreviewed_role"]
)
def test_public_schema_acl_never_relabels_a_foreign_grantor_as_its_owner(
    data_pair, grantor
):
    document = copy.deepcopy(data_pair[0])
    namespace = next(row for kind, row, _ in document["entries"] if kind == "namespace")
    namespace["nspacl"] = ["pg_database_owner=UC/" + grantor, "=U/" + grantor]
    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.normalize_catalog17_v1(document)


@pytest.mark.parametrize(
    "change",
    [
        "sequence_owned_by",
        "sequence_increment",
        "function_acl",
        "default_acl",
        "default_expression",
        "const_bytes",
        "used_collation",
        "language_handler",
        "function_body",
        "index_flags",
        "type_layout",
    ],
)
def test_semantic_catalog_drift_is_retained_or_refused(data_pair, change):
    first, _ = data_pair
    original = subject.normalize_catalog17_v1(first)
    modified = copy.deepcopy(first)
    if change == "sequence_owned_by":
        modified["dependencies"] = [
            edge
            for edge in modified["dependencies"]
            if not (edge[0] == "pg_class" and edge[6] == "a")
        ]
    elif change == "sequence_increment":
        _row(modified, "sequence")["seqincrement"] = 2
    elif change == "function_acl":
        _row(modified, "function", name=FUNCTIONS[0])["proacl"] = [
            "=X/" + modified["profile"]["owner"]
        ]
    elif change == "default_acl":
        _row(modified, "default_acl")["defaclacl"].append(
            "=r/" + modified["profile"]["owner"]
        )
    elif change == "default_expression":
        row = _row(modified, "default")
        row["adbin"] = row["adbin"].replace(":funcformat 0", ":funcformat 2")
    elif change == "const_bytes":
        _row(modified, "constraint")["conbin"] = _constant(0)
    elif change == "used_collation":
        modified["bootstrap"]["collations"][0]["collversion"] = "changed-definition"
    elif change == "language_handler":
        modified["bootstrap"]["language_callbacks"][0]["prosrc"] = (
            "changed_handler_body"
        )
    elif change == "function_body":
        _row(modified, "function", name=FUNCTIONS[0])["prosrc"] += (
            " -- " + modified["profile"]["owner"]
        )
    elif change == "type_layout":
        _row(modified, "type")["typlen"] = 4
    else:
        _row(modified, "index")["indisunique"] = False
    try:
        result = subject.normalize_catalog17_v1(modified)
    except subject.Retained0017CatalogRefused:
        return
    assert result != original


def test_actor_privilege_and_maintenance_hashes_remain_separate_without_coercion(
    data_pair,
):
    first, _ = data_pair
    original = subject.normalize_catalog17_v1(first)
    modified = copy.deepcopy(first)
    modified["actor_physical"]["owner"][2] = True
    modified["actor_physical"]["membership_count"] = 1
    modified["actor_physical"]["membership_sha256"] = "d" * 64
    modified["actor_physical"]["maintenance_settings_sha256"] = "e" * 64
    before = subject.canonical(modified)
    assert subject.normalize_catalog17_v1(modified) == original
    assert subject.canonical(modified) == before
    assert modified["actor_physical"]["owner"][2] is True
    assert "actor_physical" not in original


@pytest.mark.parametrize(
    "change",
    [
        "foreign_acl",
        "wrong_actor",
        "unsupported_dependency",
        "wrong_sequence_datum",
        "changed_owner_literal",
    ],
)
def test_typed_owner_and_sequence_binding_never_use_global_string_replacement(
    data_pair, change
):
    first, _ = data_pair
    original = subject.normalize_catalog17_v1(first)
    modified = copy.deepcopy(first)
    if change == "foreign_acl":
        _row(modified, "default_acl")["defaclacl"].append(
            "foreign=r/" + modified["profile"]["owner"]
        )
    elif change == "wrong_actor":
        modified["actor_physical"]["owner"][1] = "another_owner"
    elif change == "unsupported_dependency":
        modified["dependencies"][0][3] = "pg_foreign_table"
    elif change == "wrong_sequence_datum":
        sequence_oid = _row(modified, "sequence")["seqrelid"]
        _row(modified, "default")["adbin"] = _sequence(sequence_oid + 1)
    else:
        _row(modified, "function", name=FUNCTIONS[0])["prosrc"] += (
            " -- " + modified["profile"]["database"]
        )
        assert subject.normalize_catalog17_v1(modified) != original
        return
    with pytest.raises(subject.Retained0017CatalogRefused):
        subject.normalize_catalog17_v1(modified)
