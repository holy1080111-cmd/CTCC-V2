"""Explicit private Live runtime configuration; absent configuration stays OFF.

Loading creates four unconnected, independently restricted TLS factories. It
does not inspect an exchange, connect to PostgreSQL, clear a stop or arm Live.
There is deliberately no fallback to the application's Demo database URL.
"""

from __future__ import annotations

import os
import stat
from contextlib import asynccontextmanager
from pathlib import Path
from weakref import WeakKeyDictionary

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.native_pg_tls_v1 import (
    _read_bounded_regular_file_v1,
    verified_native_pg_tls_context_v1,
)
from app.database.repositories import native_live_financial_epoch_v1 as financial
from app.database.repositories import native_live_ledger_v1 as ledger
from app.database.repositories import native_live_operator_control_v1 as operator
from app.domain.source_primitives import canonical, decode, sha
from app.trade_evidence.native_private_root_v2 import (
    _windows_private,
    require_private_evidence_base_v2,
)

ENVIRONMENT_NAME = "CTCC_NATIVE_LIVE_RUNTIME_CONFIG_FILE"
SCHEMA = "ctcc.native_live.private_runtime.v1"
_KEY = object()
_OWNED = WeakKeyDictionary()


class LiveRuntimeNotConfigured(ValueError):
    pass


class LiveRuntimeConfigurationError(ValueError):
    pass


def _need(value):
    if value is not True:
        raise LiveRuntimeConfigurationError(
            "native_live_runtime_configuration_refused"
        ) from None


class OwnedLiveRuntimeV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _need(False)

    def __copy__(self):
        _need(False)

    def __deepcopy__(self, memo):
        _need(False)

    def __reduce_ex__(self, protocol):
        _need(False)


def _read(path, base):
    _need(
        type(path) is type(Path())
        and path.is_absolute()
        and path.resolve() == path
        and path.is_relative_to(base)
    )
    require_private_evidence_base_v2(str(path.parent))

    def identity():
        info = path.lstat()
        _need(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and not getattr(info, "st_file_attributes", 0) & 1024
        )
        if os.name == "nt":
            _windows_private(path)
        else:
            _need(info.st_uid == os.geteuid() and not info.st_mode & 0o077)
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
        )

    before = identity()
    raw = _read_bounded_regular_file_v1(path, 65536)
    _need(identity() == before)
    return raw


def _document(raw, base):
    doc = decode(raw, maximum=65536)
    _need(
        type(doc) is dict
        and set(doc)
        == {
            "schema",
            "region",
            "database_oid",
            "owner_oid",
            "ca_file",
            "ca_sha256",
            "operator",
            "writer",
            "collector",
            "reconciler",
            "journal_key_file",
            "account_source_root",
            "public_root",
            "evidence_root",
        }
        and doc["schema"] == SCHEMA
        and doc["region"] in ("global", "eea")
    )
    for key in ("database_oid", "owner_oid"):
        _need(type(doc[key]) is int and 0 < doc[key] < 2**32)
    ca = Path(doc["ca_file"])
    _need(sha(_read(ca, base)) == doc["ca_sha256"])
    roots = tuple(
        Path(doc[name])
        for name in ("account_source_root", "public_root", "evidence_root")
    )
    _need(len(set(roots)) == 3)
    for root in roots:
        _need(
            root.is_absolute() and root.resolve() == root and root.is_relative_to(base)
        )
        require_private_evidence_base_v2(str(root))
    descriptor = doc["journal_key_file"]
    _need(type(descriptor) is dict and set(descriptor) == {"path", "sha256"})
    _need(sha(_read(Path(descriptor["path"]), base)) == descriptor["sha256"])
    roles = tuple(
        doc[name] for name in ("operator", "writer", "collector", "reconciler")
    )
    oids, names, targets = [], [], []
    for role in roles:
        _need(
            type(role) is dict
            and set(role) == {"oid", "name", "dsn"}
            and type(role["oid"]) is int
            and 0 < role["oid"] < 2**32
            and role["oid"] != doc["owner_oid"]
            and type(role["name"]) is str
            and type(role["dsn"]) is str
        )
        url = make_url(role["dsn"])
        _need(
            url.drivername == "postgresql+asyncpg"
            and url.username == role["name"]
            and type(url.password) is str
            and bool(url.password)
            and type(url.host) is str
            and bool(url.host)
            and type(url.database) is str
            and bool(url.database)
            and not url.query
        )
        oids.append(role["oid"])
        names.append(role["name"])
        targets.append((url.host, url.port, url.database))
    _need(len(set(oids)) == len(set(names)) == 4 and len(set(targets)) == 1)
    return doc


def _replay_live_runtime_v1(owner, *, key):
    _need(key is _KEY and type(owner) is OwnedLiveRuntimeV1)
    state = _OWNED.get(owner)
    _need(type(state) is dict and state["pid"] == os.getpid())
    require_private_evidence_base_v2(str(state["base"]))
    _need(_read(state["path"], state["base"]) == state["raw"])
    _need(canonical(_document(state["raw"], state["base"])) == state["document_json"])
    return state


@asynccontextmanager
async def configured_live_runtime_v1():
    """Run in the same worker task as the source lease and qualification."""
    value = os.environ.get(ENVIRONMENT_NAME)
    if value is None or value == "":
        raise LiveRuntimeNotConfigured("NATIVE_LIVE_NOT_CONFIGURED")
    engines = []
    owner = None
    yielded = False
    try:
        local = os.environ.get("LOCALAPPDATA")
        _need(type(local) is str and bool(local))
        base = (Path(local) / "CTCC" / "private").resolve()
        require_private_evidence_base_v2(str(base))
        path = Path(value)
        raw = _read(path, base)
        doc = _document(raw, base)
        context = verified_native_pg_tls_context_v1(
            Path(doc["ca_file"]), doc["ca_sha256"]
        )
        factories = []
        for name in ("operator", "writer", "collector", "reconciler"):
            engine = create_async_engine(
                doc[name]["dsn"],
                poolclass=NullPool,
                hide_parameters=True,
                echo=False,
                connect_args={"ssl": context},
            )
            engines.append(engine)
            factories.append(async_sessionmaker(engine, expire_on_commit=False))
        db, own = doc["database_oid"], doc["owner_oid"]
        operator_pins = operator.LiveOperatorRolePinsV1(
            db,
            own,
            doc["operator"]["oid"],
            doc["operator"]["name"],
            doc["writer"]["oid"],
        )
        writer_pins = ledger.LiveLedgerRolePinsV1(
            db, own, doc["writer"]["oid"], doc["writer"]["name"]
        )
        collector_pins = financial.LiveFinancialCollectorRolePinsV1(
            db, own, doc["collector"]["oid"], doc["collector"]["name"]
        )
        operator._factory(factories[0], operator_pins)
        ledger._factory(factories[1], writer_pins)
        financial._factory(factories[2], collector_pins)
        from app.database.repositories.native_live_lifecycle_v1 import (
            LiveLifecycleRolePinsV1,
        )

        reconciler_pins = LiveLifecycleRolePinsV1(
            db, own, doc["reconciler"]["oid"], doc["reconciler"]["name"]
        )
        owner = object.__new__(OwnedLiveRuntimeV1)
        _OWNED[owner] = {
            "pid": os.getpid(),
            "base": base,
            "path": path,
            "raw": raw,
            "document_json": canonical(doc),
            "region": doc["region"],
            "operator_factory": factories[0],
            "writer_factory": factories[1],
            "collector_factory": factories[2],
            "operator_pins": operator_pins,
            "writer_pins": writer_pins,
            "collector_pins": collector_pins,
            "reconciler_factory": factories[3],
            "reconciler_pins": reconciler_pins,
            "journal_key_descriptor": dict(doc["journal_key_file"]),
            "source_root": Path(doc["account_source_root"]),
            "public_root": Path(doc["public_root"]),
            "evidence_root": Path(doc["evidence_root"]),
        }
        yielded = True
        yield owner
    except LiveRuntimeNotConfigured:
        raise
    except Exception:
        if yielded:
            raise
        raise LiveRuntimeConfigurationError(
            "native_live_runtime_configuration_refused"
        ) from None
    finally:
        if owner is not None:
            _OWNED.pop(owner, None)
        for engine in engines:
            await engine.dispose()


async def inhibit_configured_live_runtime_v1(owner):
    """Startup negative-only DB stop. Never call a venue or prepare a Control."""
    from app.config.settings import get_settings

    state = _replay_live_runtime_v1(owner, key=_KEY)
    settings = get_settings()
    # These are private deployment pins, not first-page account observations.
    # The operation cannot assert exchange flatness or enable the runtime.
    uid = settings.okx_live_expected_uid.get_secret_value()
    main = settings.okx_live_expected_main_uid.get_secret_value()
    readback = await operator.NativeLiveOperatorControlRepositoryV1(
        state["operator_factory"], state["operator_pins"]
    ).inhibit_configured_account_v1(account_uid=uid, main_uid=main)
    return {
        "schema": "ctcc.native_live.startup_inhibit_data.v1",
        "armed": False,
        "emergency_stop": True,
        "control_present": readback is not None,
        "readback_sha256": None if readback is None else readback.row_sha256,
        "account_complete": False,
        "exchange_flat_verified": False,
        "execution_authority": False,
    }
