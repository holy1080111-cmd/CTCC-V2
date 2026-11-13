"""Actual DB0056 inherited graph; separate wire schema and no startup seal.

Structural validation reuses the pinned DB0055 object-level helpers. The three
inherited validators that check database head are explicitly 0056 here. Actual
query records are never projected onto another actor, version or wire schema.
"""

import hashlib

from app.domain import collector_current_0055_catalog_v1 as inherited
from app.domain import controlled_history_current55_schema_v2 as history55
from app.domain.source_primitives import canonical

SCHEMA = "ctcc.collector_current0056.actual_inherited_catalog.v1"
OBSERVATION_SCHEMA = "ctcc.collector_current0056.inherited_catalog_observation.v1"
ACTOR_CATALOG_SQL = inherited.old54._once(
    inherited.ACTOR_CATALOG_SQL,
    "'schema','" + inherited.SCHEMA + "'",
    "'schema','" + SCHEMA + "'",
)
OWNER_CATALOG_SQL = inherited.old54._once(
    inherited.OWNER_CATALOG_SQL,
    "'schema','" + inherited.SCHEMA + "'",
    "'schema','" + SCHEMA + "'",
)


def _context(value, *, role, read_only, parents, schema_name):
    if (
        value["schema_version"] != schema_name
        or value["database_head"] != "0056"
        or value["actor"] != role["oid"]
        or type(value["table_oid"]) is not int
        or not 0 < value["table_oid"] < 2**32
    ):
        inherited._deny()
    server = value["server"]
    if type(server) is not dict or set(server) != {
        "version_num",
        "current_user",
        "session_user",
        "role_setting",
        "database",
        "timezone",
        "search_path",
        "isolation",
        "read_only",
        "synchronous_commit",
        "fsync",
        "full_page_writes",
        "replication_role",
        "recovery",
        "role_oid",
        "superuser",
        "can_login",
    }:
        inherited._deny()
    version = server["version_num"]
    if (
        type(version) is not str
        or not version.isascii()
        or not version.isdecimal()
        or not 170000 <= int(version) < 180000
        or server["role_oid"] != role["oid"]
        or server["current_user"] != role["name"]
        or server["session_user"] != role["name"]
        or server["superuser"] is not role["superuser"]
        or server["can_login"] is not True
        or server["recovery"] is not False
        or type(server["database"]) is not str
        or not server["database"]
    ):
        inherited._deny()
    for name, expected in (
        ("role_setting", "none"),
        ("timezone", "UTC"),
        ("search_path", "pg_catalog, public, pg_temp"),
        ("isolation", "read committed"),
        ("read_only", read_only),
        ("synchronous_commit", "on"),
        ("fsync", "on"),
        ("full_page_writes", "on"),
        ("replication_role", "origin"),
    ):
        if server[name] != expected:
            inherited._deny()
    rows = value["parent_facts"]
    if type(rows) is not list or len(rows) != len(parents):
        inherited._deny()
    identities = {}
    for row in rows:
        if (
            type(row) is not dict
            or set(row)
            != {"name", "oid", "owner", "kind", "persistence", "rls", "force_rls"}
            or row["name"] not in parents
            or row["name"] in identities
            or type(row["oid"]) is not int
            or not 0 < row["oid"] < 2**32
            or row["owner"] != value["owner"]
            or row["kind"] != "r"
            or row["persistence"] != "p"
            or row["rls"] is not False
            or row["force_rls"] is not False
        ):
            inherited._deny()
        identities[row["name"]] = row["oid"]
    return identities


def _operator_at_0056(value, *, owner, role, owner_mode, configuration):
    if (
        type(value) is not dict
        or set(value)
        != {"head", "relations", "tables", "triggers", "functions", "ri_triggers"}
        or value["head"] != ["0056"]
    ):
        inherited._deny()
    identities = inherited._relations(value["relations"], owner=owner)
    if any(
        identities.get(name) != oid
        for name, oid in {
            **configuration["table_oids"],
            **configuration["shared_table_oids"],
        }.items()
    ):
        inherited._deny()
    tables, constraints = inherited._tables(
        value["tables"],
        identities=identities,
        owner=owner,
        actor=role["oid"],
        owner_mode=owner_mode,
    )
    definitions = inherited._functions_and_triggers(
        value,
        identities=identities,
        tables=tables,
        constraints=constraints,
        owner=owner,
        actor=role["oid"],
        owner_mode=owner_mode,
    )
    ri = inherited._ri84(
        value["ri_triggers"],
        identities=identities,
        constraints=constraints,
        configuration=configuration,
    )
    actual_shape = next(
        row["read_columns"]
        for row in value["relations"]
        if row["name"] == "qualification_reservations"
    )
    return identities, ri, {**definitions, "reservation_read_columns": actual_shape}


def observe_controlled_history_0056(
    value, *, owner, actor, owner_mode, reader_role_oid=None
):
    """Actual metadata only; no caller role rewriting or native admission.

    Writer actor is from the independent actual0056 current-user profile.
    A reader is compared with its actual captured-factory OID and BOTH frame
    ACLs. Owner installation admits no runtime grants. No grant is installed.
    """
    owner, actor = history55._oid(owner), history55._oid(actor)
    if (
        type(owner_mode) is not bool
        or type(value) is not dict
        or set(value)
        != {
            "schema",
            "head",
            "database",
            "actor",
            "current_user",
            "session_user",
            "role_setting",
            "functions",
            "roles",
        }
        or value["schema"] != history55.SCHEMA
        or value["head"] != "0056"
        or type(value["actor"]) is not int
        or value["actor"] != actor
        or type(value["database"]) is not str
        or not value["database"]
        or type(value["current_user"]) is not str
        or not value["current_user"]
        or value["current_user"] != value["session_user"]
        or value["role_setting"] != "none"
        or type(value["functions"]) is not list
        or len(value["functions"]) != 6
        or type(value["roles"]) is not list
    ):
        history55._deny()
    rows = {}
    for row in value["functions"]:
        if (
            type(row) is not dict
            or set(row) != history55._FUNCTION_KEYS
            or row["name"] in rows
        ):
            history55._deny()
        rows[row["name"]] = row
    all_specs = (*history55.SPECS, *history55.ORIGINAL_SPECS)
    if set(rows) != {item[0] for item in all_specs}:
        history55._deny()
    namespace = None
    seen_oids = set()
    for name, arguments, returns, body in all_specs:
        row = rows[name]
        if history55._oid(row["oid"]) in seen_oids:
            history55._deny()
        seen_oids.add(row["oid"])
        history55._oid(row["namespace_oid"])
        history55._oid(row["owner_oid"])
        if namespace is None:
            namespace = history55._oid(row["namespace_oid"])
        if (
            row["namespace"] != "public"
            or row["namespace_oid"] != namespace
            or row["owner_oid"] != owner
            or row["arguments"]
            != ", ".join(part.split(" ", 1)[1] for part in arguments.split(","))
            or row["identity_arguments"] != arguments.replace(",", ", ")
            or row["returns"] != returns
            or row["language"] != "plpgsql"
            or row["source"] != body
            or type(row["definition"]) is not str
            or not row["definition"]
            or row["security_definer"] is not True
            or row["volatility"] != "v"
            or row["config"] != ["search_path=pg_catalog, public, pg_temp"]
            or row["kind"] != "f"
            or row["parallel"] != "u"
            or row["strict"] is not False
            or row["leakproof"] is not False
            or type(row["can_execute"]) is not bool
        ):
            history55._deny()
    grants = {name: history55._grantees(row, owner) for name, row in rows.items()}
    if grants[history55.LOCK] != {owner} or grants[history55.ORIGINALS[0]] != {owner}:
        history55._deny()
    old_reader = grants[history55.ORIGINALS[2]] - {owner}
    new_reader = grants[history55.FRAME] - {owner}
    new_writer = grants[history55.APPEND] - {owner}
    if owner_mode:
        if actor != owner or old_reader or new_reader or new_writer:
            history55._deny()
        writer_oid = raw_oid = None
    else:
        if (
            actor == owner
            or len(old_reader) != 1
            or old_reader != new_reader
            or len(new_writer) != 1
        ):
            history55._deny()
        raw_oid = next(iter(old_reader))
        writer_oid = next(iter(new_writer))
        if raw_oid == writer_oid or owner in (raw_oid, writer_oid):
            history55._deny()
        if reader_role_oid is None:
            if actor != writer_oid:
                history55._deny()
        elif history55._oid(reader_role_oid) != raw_oid or actor != raw_oid:
            history55._deny()
    roles = {}
    for role in value["roles"]:
        if (
            type(role) is not dict
            or set(role) != history55._ROLE_KEYS
            or history55._oid(role["oid"]) in roles
            or type(role["name"]) is not str
            or not role["name"]
            or role["login"] is not True
            or any(
                role[key] is not False
                for key in (
                    "superuser",
                    "createdb",
                    "createrole",
                    "replication",
                    "bypassrls",
                    "membership",
                    "create",
                )
            )
        ):
            history55._deny()
        roles[role["oid"]] = role
    all_nonowners = set().union(*(grants[name] - {owner} for name in grants))
    if set(roles) != all_nonowners:
        history55._deny()
    # Historical metadata grants on the old stopped append remain separate.
    # They cannot invoke the new writer RPC; all their actual flags are read.
    if owner_mode:
        if roles:
            history55._deny()
    elif not {raw_oid, writer_oid} <= set(roles):
        history55._deny()
    if any(rows[name]["can_execute"] is not (actor in grants[name]) for name in rows):
        history55._deny()
    return {
        "definitions": {name: rows[name]["definition"] for name in history55.FUNCTIONS},
        "writer_role_oid": writer_oid,
        "raw_role_oid": raw_oid,
        "function_oids": {name: rows[name]["oid"] for name in history55.FUNCTIONS},
        "account_complete": False,
        "execution_authority": False,
    }


def _observe_inherited_graph(value, *, owner_mode, read_only):
    if (
        type(value) is not dict
        or set(value) != inherited.CATALOG_KEYS
        or value["schema"] != SCHEMA
    ):
        inherited._deny()
    role = value["role"]
    if (
        type(role) is not dict
        or set(role)
        != {
            "oid",
            "name",
            "current_user",
            "session_user",
            "setting",
            "login",
            "superuser",
            "createdb",
            "createrole",
            "replication",
            "bypassrls",
            "membership",
            "create",
        }
        or type(role["oid"]) is not int
        or not 0 < role["oid"] < 2**32
        or type(role["name"]) is not str
        or not role["name"]
        or role["current_user"] != role["name"]
        or role["session_user"] != role["name"]
        or role["setting"] != "none"
        or role["login"] is not True
        or role["superuser"] is not owner_mode
    ):
        inherited._deny()
    if not owner_mode and any(
        role[key] is not False
        for key in (
            "createdb",
            "createrole",
            "replication",
            "bypassrls",
            "membership",
            "create",
        )
    ):
        inherited._deny()
    if type(value["issued"]) is not dict:
        inherited._deny()
    owner = inherited._oid(value["issued"]["owner"])
    configuration = inherited.native_configuration.observe_catalog(
        value["native_configuration55"],
        owner=owner,
        actor=role["oid"],
        owner_mode=owner_mode,
    )
    identities, combined, operator_defs = _operator_at_0056(
        value["operator55"],
        owner=owner,
        role=role,
        owner_mode=owner_mode,
        configuration=configuration,
    )
    controlled_defs = observe_controlled_history_0056(
        value["controlled_history55"],
        owner=owner,
        actor=role["oid"],
        owner_mode=owner_mode,
    )
    submission_def = inherited._submission55(value["native_submission55"], owner=owner)
    closure_defs = inherited._closure55(
        value["native_closure55"], owner=owner, configuration=configuration
    )
    terminal_defs = inherited.native_terminal.observe_catalog(
        value["native_terminal55"], owner=owner
    )
    report_defs = inherited.native_report.observe_catalog(
        value["native_report55"], owner=owner, actor=role["oid"], owner_mode=owner_mode
    )
    close_intent_defs = inherited._close_intent55(
        value["native_close_intent55"],
        owner=owner,
        actor=role["oid"],
        owner_mode=owner_mode,
        configuration=configuration,
    )
    # Only now reuse unchanged54 body/table structural validators. Their old
    # native admission functions and head54 predicates are never called.
    guard_body, immutable_body = inherited._source_bodies55()
    current, previous = value["issued"], value["lineage"]
    inherited.old54._structure(
        current,
        owner_mode=owner_mode,
        columns=inherited.old54._ISSUED_COLUMNS,
        keys=inherited.old54._ISSUED_KEYS,
        checks=inherited.old54._ISSUED_CHECKS,
        triggers=inherited.old54._ISSUED_TRIGGERS,
        functions=(
            (inherited.issued.GUARD, guard_body, True),
            (inherited.issued.IMMUTABLE, immutable_body, False),
        ),
    )
    inherited.old54._structure(
        previous,
        owner_mode=owner_mode,
        columns=inherited.lineage.COLUMNS,
        keys=inherited.lineage.KEYS,
        checks=inherited.lineage.CHECKS,
        triggers=inherited.lineage.TRIGGERS,
        functions=(
            (inherited.lineage.GUARD, inherited.lineage.GUARD_BODY, True),
            (inherited.lineage.IMMUTABLE, inherited.lineage.IMMUTABLE_BODY, False),
        ),
    )
    if (
        current["owner"] != previous["owner"]
        or current["table_oid"] == previous["table_oid"]
    ):
        inherited._deny()
    if owner_mode:
        if role["oid"] != current["owner"]:
            inherited._deny()
    else:
        # Unchanged restricted actor verifier, actual writer query only. The
        # complete independently measured lineage_base remains untouched.
        inherited.lineage.verify_catalog(value["lineage_base"])
        if any(
            value["lineage_base"][key] != previous[key] for key in inherited.BASE_KEYS
        ):
            inherited._deny()
    current_parents = _context(
        current,
        role=role,
        read_only=read_only,
        parents=inherited.PARENTS,
        schema_name="ctcc.collector_current0054.actual_issued_catalog.v1",
    )
    previous_parents = _context(
        previous,
        role=role,
        read_only=read_only,
        parents=inherited.old53.PARENTS,
        schema_name="ctcc.collector_current0053.actual_owner_catalog.v1",
    )
    if (
        current_parents[inherited.lineage.TABLE] != previous["table_oid"]
        or any(
            current_parents[key] != previous_parents[key]
            for key in inherited.old53.PARENTS
        )
        or current["server"]["database"] != previous["server"]["database"]
    ):
        inherited._deny()
    if value["table_guards"] != [
        [inherited.lineage.TABLE, True, True, True, True],
        [inherited.issued.TABLE, True, True, True, True],
    ]:
        inherited._deny()
    expected_defaults = []
    for table, columns in (
        (inherited.lineage.TABLE, inherited.lineage.COLUMNS),
        (inherited.issued.TABLE, inherited.old54._ISSUED_COLUMNS),
    ):
        for number, column in enumerate(columns, 1):
            expected_defaults.append(
                [
                    table,
                    number,
                    column[0],
                    "clock_timestamp()" if column[0] == "db_recorded_at" else None,
                    "",
                    "",
                ]
            )
    if value["defaults"] != expected_defaults:
        inherited._deny()
    inherited.old54._acl(
        value["acl"], owner=current["owner"], actor=role["oid"], owner_mode=owner_mode
    )
    current_defs = inherited.old54._definitions(
        current,
        keys=inherited.old54._ISSUED_KEYS,
        checks=inherited.old54._ISSUED_CHECKS,
        functions=(inherited.issued.GUARD, inherited.issued.IMMUTABLE),
        triggers=inherited.old54._ISSUED_TRIGGERS,
    )
    previous_defs = inherited.old54._definitions(
        previous,
        keys=inherited.lineage.KEYS,
        checks=inherited.lineage.CHECKS,
        functions=(inherited.lineage.GUARD, inherited.lineage.IMMUTABLE),
        triggers=inherited.lineage.TRIGGERS,
    )
    scope_defs = inherited.old54._scope_parent(
        value["scope_parent"],
        owner=current["owner"],
        table_oid=current_parents["qualification_account_scopes"],
    )
    if any(current_parents[name] != identities[name] for name in inherited.PARENTS):
        inherited._deny()
    if (
        current["table_oid"] != identities[inherited.issued.TABLE]
        or previous["table_oid"] != identities[inherited.lineage.TABLE]
    ):
        inherited._deny()
    # Both independently queried component RI populations must equal the
    # corresponding rows of the already-validated global60 closure. No incoming
    # ACK edge is filtered away or trusted from a caller-provided list.
    for component in (current, previous):
        rows = component["ri_triggers"]
        if type(rows) is not list:
            inherited._deny()
        actual = {}
        for row in rows:
            if (
                type(row) is not dict
                or set(row) != inherited.old54._RI_FIELDS
                or inherited._oid(row["trigger_oid"]) in actual
            ):
                inherited._deny()
            actual[row["trigger_oid"]] = row
        expected = {
            oid: row
            for oid, row in combined.items()
            if row["child_oid"] == component["table_oid"]
            or row["relation_oid"] == component["table_oid"]
        }
        if actual != expected:
            inherited._deny()
    inherited.old54._incoming_custody(current)
    inherited.old54._incoming_custody(previous)
    if current["custody_incoming"] != previous["custody_incoming"]:
        inherited._deny()
    custody_oid = current_parents["public_runtime_raw_bundles_v1"]
    expected_internal = sorted(
        [
            row["trigger_oid"],
            row["name"],
            row["function_oid"],
            row["constraint_oid"],
            row["relation_oid"],
            row["other_oid"],
        ]
        for row in combined.values()
        if row["relation_oid"] == custody_oid
    )
    if len(expected_internal) != 4 or value["custody_internal"] != expected_internal:
        inherited._deny()
    if current_defs["custody_triggers"] != previous_defs["custody_triggers"]:
        inherited._deny()
    native_sha256 = hashlib.sha256(
        canonical(
            {
                "issued": current_defs,
                "lineage": previous_defs,
                "scope_parent": scope_defs,
                "operator55": operator_defs,
                "controlled_history55": controlled_defs["definitions"],
                "native_submission55": submission_def,
                "native_closure55": closure_defs,
                "native_terminal55": terminal_defs,
                "native_report55": report_defs,
                "native_close_intent55": close_intent_defs,
                "native_configuration55": configuration["definitions"],
            }
        )
    ).hexdigest()
    return {
        "schema": OBSERVATION_SCHEMA,
        "database_head": "0056",
        "legacy_0054_issuer_ddl_sha256": inherited.issued.DDL_SHA256,
        "legacy_0053_lineage_ddl_sha256": inherited.lineage.DDL_SHA256,
        "operator_ddl_sha256": inherited.FROZEN_OPERATOR_DDL_SHA256,
        "controlled_history_ddl_sha256": inherited.controlled_history.DDL_SHA256,
        "native_submission_body_sha256": inherited.native_submission.CURRENT_BODY_SHA256,
        "native_closure_body_sha256": inherited.native_closure.CURRENT_BODY_SHA256,
        "native_zero_closure_body_sha256": inherited.native_closure.ZERO_BODY_SHA256,
        "native_filled_flat_body_sha256": terminal_defs[
            "qualification_native_filled_flat_data_v55"
        ]["body_sha256"],
        "native_report_eligibility_body_sha256": inherited.native_report.schema.body_sha(
            inherited.native_report.schema.ELIGIBLE_SQL, "$ctcc$"
        ),
        "native_close_intent_index_sha256": inherited.native_close_intent.INDEX_SPEC_SHA256,
        "native_configuration_ddl_sha256": inherited.native_configuration.DDL_SHA256,
        "native_configuration_positive_body_sha256": inherited.configuration_enforcement.BODY_SHA256,
        "native_definitions_sha256": native_sha256,
        "role_oid": role["oid"],
        "native_baseline_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }


def _observe_readonly(value, *, owner_mode):
    try:
        return _observe_inherited_graph(value, owner_mode=owner_mode, read_only="on")
    except inherited.Current0055CatalogError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        inherited._deny()


def observe_writer_0056(value):
    """Validate the actual DB0056 writer record in a read-only transaction."""
    return _observe_readonly(value, owner_mode=False)


def observe_readwrite_writer_0056(value):
    """Validate the inherited DB0056 graph for a restricted writer in READ WRITE."""
    try:
        return _observe_inherited_graph(value, owner_mode=False, read_only="off")
    except inherited.Current0055CatalogError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        inherited._deny()


def observe_owner_installation_0056(value):
    """Validate the actual read-only pre-grant owner graph, without admission."""
    return _observe_readonly(value, owner_mode=True)


def observe_owner_readwrite_installation_0056(value):
    """Validate the actual pre-grant owner graph in a write transaction."""
    try:
        return _observe_inherited_graph(value, owner_mode=True, read_only="off")
    except inherited.Current0055CatalogError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        inherited._deny()
