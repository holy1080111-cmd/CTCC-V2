"""Stdlib-only source17 contract. Parse inert Python AST, never import migrations."""

from __future__ import annotations

import ast
import configparser
import hashlib
import json
import re
import stat
from pathlib import Path

_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z", re.ASCII)
_FILE = re.compile(r"([0-9]{4})_[a-z0-9_]+\.py\Z", re.ASCII)


class Retained0017SourceContractRefused(ValueError):
    pass


def _need(value):
    if value is not True:
        raise Retained0017SourceContractRefused(
            "retained0017_source_contract_refused"
        ) from None


def column_expressions_v1(arguments, module_ast):
    result = []
    for column in arguments:
        if isinstance(column, ast.Starred):
            call = column.value
            _need(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "_timestamps"
                and not call.args
                and not call.keywords
            )
            helpers = [
                node
                for node in module_ast.body
                if isinstance(node, ast.FunctionDef) and node.name == "_timestamps"
            ]
            _need(len(helpers) == 1 and len(helpers[0].body) == 1)
            returned = helpers[0].body[0]
            _need(
                isinstance(returned, ast.Return)
                and isinstance(returned.value, ast.List)
            )
            result.extend(returned.value.elts)
        else:
            result.append(column)
    return result


def original_operations_v1(statements, bindings=None):
    """Only source-literal loops, in original DDL order, without execution."""
    bound = {} if bindings is None else bindings
    for statement in statements:
        if isinstance(statement, ast.For):
            _need(
                isinstance(statement.target, ast.Name)
                and isinstance(statement.iter, (ast.List, ast.Tuple))
                and not statement.orelse
            )
            for item in statement.iter.elts:
                _need(isinstance(item, ast.Constant) and type(item.value) is str)
                yield from original_operations_v1(
                    statement.body, bound | {statement.target.id: item.value}
                )
        elif isinstance(statement, ast.Expr):
            node = statement.value
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "op"
            ):
                yield node, bound
            else:
                _need(isinstance(node, ast.Constant) and type(node.value) is str)
        else:
            _need(isinstance(statement, ast.Pass))


def minimum_columns17_v1(source_rows):
    """Shared minimum-shape codec for the reader and structural comparator."""
    _need(type(source_rows) in (list, tuple) and len(source_rows) == 17)
    shape = {"alembic_version": ["version_num"]}
    for expected, row in enumerate(source_rows, 1):
        _need(
            type(row) in (list, tuple)
            and len(row) == 2
            and row[0] == f"{expected:04d}"
            and type(row[1]) is bytes
        )
        tree = ast.parse(row[1].decode("utf-8"))
        bodies = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        ]
        _need(len(bodies) == 1)
        for node, bindings in original_operations_v1(bodies[0].body):
            if node.func.attr not in {"create_table", "add_column"}:
                _need(
                    node.func.attr not in {"drop_column", "drop_table", "rename_table"}
                )
                continue
            _need(
                bool(node.args)
                and isinstance(node.args[0], ast.Constant)
                and type(node.args[0].value) is str
            )
            name = node.args[0].value
            if node.func.attr == "create_table":
                _need(name not in shape)
                shape[name] = []
            _need(name in shape)
            for column in column_expressions_v1(node.args[1:], tree):
                if (
                    isinstance(column, ast.Call)
                    and isinstance(column.func, ast.Attribute)
                    and column.func.attr == "Column"
                ):
                    _need(
                        bool(column.args)
                        and (
                            (
                                isinstance(column.args[0], ast.Constant)
                                and type(column.args[0].value) is str
                            )
                            or (
                                isinstance(column.args[0], ast.Name)
                                and column.args[0].id in bindings
                            )
                        )
                    )
                    key = (
                        column.args[0].value
                        if isinstance(column.args[0], ast.Constant)
                        else bindings[column.args[0].id]
                    )
                    _need(key not in shape[name])
                    shape[name].append(key)
    _need(
        all(
            _NAME.fullmatch(name)
            and values
            and len(values) == len(set(values))
            and all(_NAME.fullmatch(c) for c in values)
            for name, values in shape.items()
        )
    )
    return shape


def _read(path):
    before = path.lstat()
    _need(
        stat.S_ISREG(before.st_mode)
        and before.st_nlink == 1
        and not getattr(before, "st_file_attributes", 0) & 0x400
        and 0 < before.st_size <= 2 * 1024 * 1024
    )
    raw = path.read_bytes()
    after = path.lstat()
    _need(
        (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        == (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        and len(raw) == before.st_size
    )
    return raw


def _revision(tree):
    values = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        else:
            continue
        if name in {"revision", "down_revision", "branch_labels", "depends_on"}:
            _need(name not in values and isinstance(node.value, ast.Constant))
            values[name] = node.value.value
    _need(
        set(values) >= {"revision", "down_revision"}
        and values.get("branch_labels") is None
        and values.get("depends_on") is None
    )
    return values["revision"], values["down_revision"]


def source17_contract_from_root_v1(root):
    """Same58 graph and canonical-LF17 prefix as the owner reader, no packages."""
    _need(type(root) is type(Path()) and root.is_absolute())
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(_read(root / "alembic.ini").decode("utf-8"))
    _need(
        config.get("alembic", "script_location") == "migrations"
        and not config.get("alembic", "version_locations", fallback=None)
    )
    versions = root / "migrations" / "versions"
    _need(
        versions.is_dir() and not versions.is_symlink() and not versions.is_junction()
    )
    sources = {}
    for path in sorted(versions.rglob("*.py")):
        if path.name == "__init__.py":
            _need(path.parent == versions)
            continue
        match = _FILE.fullmatch(path.name)
        _need(match is not None and path.parent == versions)
        raw = _read(path)
        revision, previous = _revision(ast.parse(raw.decode("utf-8")))
        _need(
            type(revision) is str and revision == match[1] and revision not in sources
        )
        sources[revision] = (previous, path.relative_to(root).as_posix(), raw)
    _need(set(sources) == {f"{i:04d}" for i in range(1, 59)})
    for number in range(1, 59):
        _need(
            sources[f"{number:04d}"][0] == (f"{number - 1:04d}" if number > 1 else None)
        )
    prefix = []
    rows = []
    for number in range(1, 18):
        revision = f"{number:04d}"
        _, name, raw = sources[revision]
        canonical_raw = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        prefix.append([revision, name, hashlib.sha256(canonical_raw).hexdigest()])
        rows.append((revision, raw))
    encoded = json.dumps(
        prefix, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return {
        "columns": minimum_columns17_v1(rows),
        "source17_prefix_sha256": hashlib.sha256(encoded).hexdigest(),
    }
