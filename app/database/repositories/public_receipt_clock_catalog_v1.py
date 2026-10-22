"""Source-declared DB0024/0035/0047 catalog policy, never DB learned values.

The small expression reader supports only these CHECK/partial-index predicates.
It preserves operator precedence and grouping. It is not a general SQL parser.
Runtime database/catalog results can be compared with this policy, never extend
it. Function body digests are SHA256 of exact migration dollar-quoted prosrc.
"""

from __future__ import annotations

import re

WITNESS = "public_receipt_witness_revisions"
OBSERVATION = "public_receipt_post_read_observations"
TABLES = (WITNESS, OBSERVATION)
FUNCTION_HASHES = {
    "public_receipt_witness_insert_guard": "74cf98c2c28cfee8cd63d24213957876a602373f65c948f2b4f83b7e466c4e81",
    "public_receipt_witness_immutable": "bbcafd05fca41bf9269dee5a187344a400b718fa301ff48b6757b996487ae01f",
    "public_receipt_witness_append": "e480369597ef04265e650ae4a1098869c431c028968ad14e3d91d2d435f4cdc0",
    "public_receipt_witness_read": "f46ee8b9ccc9e3594d12a7490b9fd68c9dc7d6dfd905f3a864bd490c07d9aefb",
    "public_receipt_post_read_insert_guard": "ea0f8b8e5265f0e221ca1ea0266ae33d29bcb60aad4226eaa9b382bbfa374a51",
    "public_receipt_post_read_immutable": "3d791114c3e4023a044cbcce43d197b32c0e5b09d2eeed08801ad54275813685",
    "public_receipt_post_read_append": "d7a5f0e44fd77bd7ad5300b224a39d744272c857611b856344bc29f061282868",
    "public_receipt_post_read_read": "3e7d974f4d5b8966194682c22c6151d4869f1953ffeb7582f852c5b522eba943",
}
FUNCTION_METADATA = {
    "public_receipt_witness_insert_guard": (
        False,
        "plpgsql",
        "trigger",
        False,
        "",
        "v",
    ),
    "public_receipt_witness_immutable": (False, "plpgsql", "trigger", False, "", "v"),
    "public_receipt_witness_append": (True, "plpgsql", "void", False, "jsonb", "v"),
    "public_receipt_witness_read": (True, "sql", WITNESS, True, "text", "s"),
    "public_receipt_post_read_insert_guard": (
        False,
        "plpgsql",
        "trigger",
        False,
        "",
        "v",
    ),
    "public_receipt_post_read_immutable": (False, "plpgsql", "trigger", False, "", "v"),
    "public_receipt_post_read_append": (
        True,
        "plpgsql",
        "void",
        False,
        "text, bigint, text, text, text, text, text, text, text",
        "v",
    ),
    "public_receipt_post_read_read": (
        True,
        "sql",
        OBSERVATION,
        True,
        "text, bigint",
        "s",
    ),
}
COLUMNS = {
    WITNESS: (
        ("journal_key", "character varying(64)", True),
        ("revision", "bigint", True),
        ("journal_id", "character varying(32)", True),
        ("root_device", "numeric", True),
        ("root_inode", "numeric", True),
        ("transition", "character varying(24)", True),
        ("state", "character varying(24)", True),
        ("operation_id", "character varying(32)", False),
        ("plan_sha256", "character varying(64)", False),
        ("attempt_outcome", "character varying(24)", False),
        ("previous_record_sha256", "character varying(64)", False),
        ("checkpoint_json", "text", True),
        ("checkpoint_sha256", "character varying(64)", True),
        ("record_sha256", "character varying(64)", True),
        ("recorded_at", "timestamp with time zone", True),
    ),
    OBSERVATION: (
        ("journal_key", "character varying(64)", True),
        ("witness_revision", "bigint", True),
        ("witness_record_sha256", "character varying(64)", True),
        ("checkpoint_sha256", "character varying(64)", True),
        ("capture_sequence", "bigint", True),
        ("capture_head_sha256", "character varying(64)", True),
        ("capture_id", "character varying(32)", True),
        ("plan_sha256", "character varying(64)", True),
        ("receipt_sha256", "character varying(64)", True),
        ("source_rows_sha256", "character varying(64)", True),
        ("v2_capture_sha256", "character varying(64)", True),
        ("witness_recorded_at", "timestamp with time zone", True),
        ("observed_at", "timestamp with time zone", True),
    ),
}
CHECKS = {
    WITNESS: {
        "ck_public_receipt_witness_revisions_revision_bound": "revision BETWEEN 0 AND 8192",
        "ck_public_receipt_witness_revisions_identity": """journal_key ~ '^[a-f0-9]{64}$' AND journal_id ~ '^[a-f0-9]{32}$'
          AND checkpoint_sha256 ~ '^[a-f0-9]{64}$' AND record_sha256 ~ '^[a-f0-9]{64}$'
          AND (previous_record_sha256 IS NULL OR previous_record_sha256 ~ '^[a-f0-9]{64}$')
          AND (plan_sha256 IS NULL OR plan_sha256 ~ '^[a-f0-9]{64}$')
          AND (operation_id IS NULL OR operation_id ~ '^[a-f0-9]{32}$')""",
        "ck_public_receipt_witness_revisions_checkpoint_bound": "root_device >= 0 AND root_inode >= 1 AND octet_length(checkpoint_json) BETWEEN 1 AND 4096",
        "ck_public_receipt_witness_revisions_state": """transition IN ('initialize','open_attempt','append_attempt','append_capture','close_rejected_attempt')
          AND state IN ('idle','open','attempt_anchored') AND (attempt_outcome IS NULL OR attempt_outcome IN ('completed_collection','rejected','incomplete'))""",
        "ck_public_receipt_witness_revisions_native_identity_bounds": """root_device BETWEEN 0 AND 18446744073709551615 AND root_device=trunc(root_device)
          AND root_inode BETWEEN 1 AND 340282366920938463463374607431768211455 AND root_inode=trunc(root_inode)""",
    },
    OBSERVATION: {
        "ck_public_receipt_post_read_hashes": """journal_key ~ '^[a-f0-9]{64}$' AND witness_record_sha256 ~ '^[a-f0-9]{64}$'
          AND checkpoint_sha256 ~ '^[a-f0-9]{64}$' AND capture_head_sha256 ~ '^[a-f0-9]{64}$'
          AND capture_id ~ '^[a-f0-9]{32}$' AND plan_sha256 ~ '^[a-f0-9]{64}$'
          AND receipt_sha256 ~ '^[a-f0-9]{64}$' AND source_rows_sha256 ~ '^[a-f0-9]{64}$' AND v2_capture_sha256 ~ '^[a-f0-9]{64}$'""",
        "ck_public_receipt_post_read_bounds": "witness_revision BETWEEN 3 AND 8192 AND capture_sequence BETWEEN 1 AND 1024",
        "ck_public_receipt_post_read_time": "witness_recorded_at <= observed_at",
    },
}
KEYS = {
    WITNESS: {"pk_public_receipt_witness_revisions": ("p", [1, 2])},
    OBSERVATION: {
        "pk_public_receipt_post_read_observations": ("p", [1, 2]),
        "uq_public_receipt_post_read_capture": ("u", [1, 5]),
        "fk_public_receipt_post_read_witness": ("f", [1, 2]),
    },
}
INDEXES = {
    WITNESS: {
        "pk_public_receipt_witness_revisions": ([1, 2], ["text_ops", "int8_ops"], None),
        "uq_public_receipt_witness_operation_start": (
            [1, 8],
            ["text_ops", "text_ops"],
            "transition='open_attempt'",
        ),
    },
    OBSERVATION: {
        "pk_public_receipt_post_read_observations": (
            [1, 2],
            ["text_ops", "int8_ops"],
            None,
        ),
        "uq_public_receipt_post_read_capture": ([1, 5], ["text_ops", "int8_ops"], None),
    },
}

_TOKEN = re.compile(
    r"\s*(?:('(?:[^']|'')*')|([0-9]+)|([a-zA-Z_][a-zA-Z_0-9]*(?:\.[a-zA-Z_][a-zA-Z_0-9]*)?)|(::|<=|>=|<>|[=~<>()\[\],]))"
)


class CatalogExpressionError(ValueError):
    pass


def expression_tree(raw: str, table: str):
    """Strict bounded grammar; only source-native coercions are removed.

    INTEGER literal to numeric/textual-numeric and varchar→text casts preserve
    these source operands. Other casts/functions/identifiers are rejected.
    IN and PostgreSQL's = ANY ARRAY spelling map to the same ordered AST.
    """
    if type(raw) is not str or not 0 < len(raw) <= 8192 or table not in COLUMNS:
        raise CatalogExpressionError("catalog_expression_invalid")
    tokens, offset = [], 0
    while offset < len(raw):
        if not raw[offset:].strip():
            break
        match = _TOKEN.match(raw, offset)
        if match is None:
            raise CatalogExpressionError("catalog_expression_invalid")
        tokens.append(next(item for item in match.groups() if item is not None))
        offset = match.end()
    if not tokens or len(tokens) > 1024:
        raise CatalogExpressionError("catalog_expression_invalid")
    columns = {name: kind for name, kind, _ in COLUMNS[table]}
    position, depth = 0, 0

    def peek(value=None):
        item = tokens[position].lower() if position < len(tokens) else None
        return item if value is None else item == value

    def take(value=None):
        nonlocal position
        if position >= len(tokens) or value is not None and not peek(value):
            raise CatalogExpressionError("catalog_expression_invalid")
        result = tokens[position]
        position += 1
        return result

    def cast(node):
        while peek("::"):
            take("::")
            target = take().lower()
            if target == "character":
                take("varying")
                target = "varchar"
            if target not in ("text", "varchar", "numeric", "bigint", "integer"):
                raise CatalogExpressionError("catalog_expression_cast_invalid")
            is_array = peek("[")
            if is_array:
                take("[")
                take("]")
                if node[0] != "array" or target not in ("text", "varchar"):
                    raise CatalogExpressionError("catalog_expression_cast_invalid")
            elif node[0] == "column":
                source = columns[node[1]]
                if not (
                    target == "text"
                    and (source == "text" or source.startswith("character varying"))
                ):
                    raise CatalogExpressionError("catalog_expression_cast_invalid")
            elif node[0] == "string" and target in ("numeric", "bigint", "integer"):
                if not re.fullmatch(r"0|[1-9][0-9]*", node[1]):
                    raise CatalogExpressionError("catalog_expression_cast_invalid")
                node = ("number", int(node[1]))
            elif not (
                node[0] == "string"
                and target in ("text", "varchar")
                or node[0] == "number"
                and target in ("numeric", "bigint", "integer")
            ):
                raise CatalogExpressionError("catalog_expression_cast_invalid")
        return node

    def primary():
        nonlocal depth
        depth += 1
        if depth > 64:
            raise CatalogExpressionError("catalog_expression_depth_invalid")
        token = take()
        lower = token.lower()
        if token == "(":
            node = disjunction()
            take(")")
        elif token.startswith("'"):
            node = ("string", token[1:-1].replace("''", "'"))
        elif token.isdigit():
            node = ("number", int(token))
        elif lower == "array":
            take("[")
            items = [primary()]
            while peek(","):
                take(",")
                items.append(primary())
            take("]")
            node = ("array", tuple(items))
        elif lower in columns:
            node = ("column", lower)
        elif lower in (
            "octet_length",
            "trunc",
            "pg_catalog.octet_length",
            "pg_catalog.trunc",
        ):
            take("(")
            argument = primary()
            take(")")
            node = ("call", lower.rsplit(".", 1)[-1], argument)
        else:
            raise CatalogExpressionError("catalog_expression_name_invalid")
        depth -= 1
        return cast(node)

    def comparison():
        left = primary()
        if peek("between"):
            take("between")
            low = primary()
            take("and")
            high = primary()
            return ("and", (">=", left, low), ("<=", left, high))
        if peek("in"):
            take("in")
            take("(")
            items = [primary()]
            while peek(","):
                take(",")
                items.append(primary())
            take(")")
            return ("in", left, tuple(items))
        if peek("is"):
            take("is")
            take("null")
            return ("is_null", left)
        if peek() in ("=", "~", "<", ">", "<=", ">=", "<>"):
            operator = take()
            if peek("any"):
                if operator != "=":
                    raise CatalogExpressionError("catalog_expression_any_invalid")
                take("any")
                take("(")
                right = primary()
                take(")")
                if right[0] != "array":
                    raise CatalogExpressionError("catalog_expression_any_invalid")
                return ("in", left, right[1])
            return (operator, left, primary())
        return left

    def conjunction():
        node = comparison()
        while peek("and"):
            take("and")
            node = ("and", node, comparison())
        return node

    def disjunction():
        node = conjunction()
        while peek("or"):
            take("or")
            node = ("or", node, conjunction())
        return node

    def flatten(node):
        if not isinstance(node, tuple):
            return node
        if node and node[0] in ("and", "or"):
            kind = node[0]
            items = []
            for item in node[1:]:
                item = flatten(item)
                items.extend(item[1:] if item[0] == kind else (item,))
            return (kind, *items)
        return tuple(flatten(item) for item in node)

    result = disjunction()
    if position != len(tokens):
        raise CatalogExpressionError("catalog_expression_suffix_invalid")
    return flatten(result)
