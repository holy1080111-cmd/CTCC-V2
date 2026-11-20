"""Owner-only key installation primitive for the reviewed stopped setup route.

This does not create a role, grant a privilege, clear a stop or enable Live.
The caller must first complete the release's owner/source preflight. A private
key file is retained after any late/unknown failure; no automatic rotation.
"""

import os
import re
import secrets
import ssl
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.database.native_pg_tls_v1 import _read_bounded_regular_file_v1
from app.database.repositories import native_live_ledger_v1 as ledger
from app.database.repositories import native_live_lifecycle_v1 as lifecycle
from app.domain import native_live_lifecycle_schema_v1 as spec
from app.domain.native_live_intent_v1 import live_account_lock_key_v1
from app.domain.source_primitives import canonical, decode, sha
from app.okx_live import lifecycle_key_v1 as keys
from app.trade_evidence.native_private_root_v2 import (
    _open_new_windows_private_evidence_file,
)
from app.trade_evidence.storage import _WindowsDirectory, _write_fd
from app.trade_qualification.account_native_proof_storage import _root_context


class LiveLifecycleKeySetupError(ValueError):
    pass


class LiveLifecycleKeySetupCommitUnknown(RuntimeError):
    pass


def _need(condition):
    if condition is not True:
        raise LiveLifecycleKeySetupError(
            "native_live_lifecycle_key_setup_refused"
        ) from None


@dataclass(frozen=True, slots=True, repr=False)
class LiveLifecycleOwnerPinsV1:
    database_oid: int
    owner_oid: int
    owner_name: str
    reconciler_oid: int
    reconciler_name: str


def _factory(factory, pins):
    from app.database.repositories.public_runtime_bundle_custody import _closure

    _need(
        type(factory) is async_sessionmaker and type(pins) is LiveLifecycleOwnerPinsV1
    )
    _need(
        all(
            type(value) is int and 0 < value < 2**32
            for value in (pins.database_oid, pins.owner_oid, pins.reconciler_oid)
        )
        and pins.owner_oid != pins.reconciler_oid
    )
    _need(
        all(
            type(name) is str
            and re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) is not None
            and name != "public"
            and not name.startswith("pg_")
            for name in (pins.owner_name, pins.reconciler_name)
        )
    )
    engine = factory.kw.get("bind")
    _need(
        type(engine) is AsyncEngine
        and type(engine.sync_engine.pool) is NullPool
        and engine.sync_engine.hide_parameters is True
        and engine.dialect.name == "postgresql"
        and engine.dialect.driver == "asyncpg"
        and engine.url.username == pins.owner_name
        and not engine.url.query
    )
    tls = _closure(engine.sync_engine.pool._creator).get("cparams", {}).get("ssl")
    _need(
        type(tls) is ssl.SSLContext
        and tls.check_hostname is True
        and tls.verify_mode == ssl.CERT_REQUIRED
        and tls.minimum_version >= ssl.TLSVersion.TLSv1_2
        and tls.keylog_filename is None
        and tls._msg_callback is None
        and tls.sni_callback is None
    )


async def _profile(session, pins):
    row = tuple((await session.execute(ledger._PROFILE)).one())
    _need(
        row[:11]
        == (
            pins.owner_oid,
            pins.owner_name,
            True,
            "none",
            pins.database_oid,
            pins.owner_oid,
            ["0058"],
            True,
            True,
            True,
            True,
        )
    )
    role = (
        await session.execute(
            text("""SELECT oid::bigint,rolname::text,rolcanlogin,
 NOT(rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls),
 NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members WHERE member=r.oid OR roleid=r.oid)
 FROM pg_catalog.pg_roles r WHERE oid=:oid"""),
            {"oid": pins.reconciler_oid},
        )
    ).one()
    _need(tuple(role) == (pins.reconciler_oid, pins.reconciler_name, True, True, True))
    await lifecycle._catalog(
        session,
        lifecycle.LiveLifecycleRolePinsV1(
            pins.database_oid, pins.owner_oid, pins.reconciler_oid, pins.reconciler_name
        ),
    )
    actual = await session.scalar(
        text("""SELECT encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname='native_live_reservation_guard_v1' AND p.proargtypes=''::oidvector
 AND p.proowner=:owner AND NOT p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]"""),
        {"owner": pins.owner_oid},
    )
    _need(actual == sha(spec.RESERVATION_GUARD_SQL.split("$body$")[1].encode("utf8")))


def _existing_file(path):
    keys._private_file(path)
    raw = _read_bounded_regular_file_v1(path, 4096)
    document = decode(raw, maximum=4096)
    _need(
        canonical(document) == raw
        and keys.key_identity_v1(document) == document["key_id"]
    )
    return raw, document


def _create_file(path, raw):
    _need(
        type(path) is type(Path())
        and path.is_absolute()
        and path.resolve() == path
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", path.name) is not None
    )
    with _root_context(path.parent) as directory:
        if type(directory) is _WindowsDirectory:
            fd = _open_new_windows_private_evidence_file(directory, path.name)
            try:
                _write_fd(fd, raw)
            finally:
                os.close(fd)
        else:
            directory.publish(path.name, raw)
    actual, _ = _existing_file(path)
    _need(actual == raw)


async def _install_owner_live_lifecycle_key_v1(
    factory, pins, *, path, account_uid, main_uid, origin
):
    """Install/re-read one immutable binding; return only a private descriptor."""
    _factory(factory, pins)
    _need(type(path) is type(Path()) and path.is_absolute() and path.resolve() == path)
    _need(not any((parent / ".git").exists() for parent in path.parents))
    _need(
        not any(
            parent.name.lower()
            in ("reports", "logs", "validation-results", "dist", "releases")
            for parent in path.parents
        )
    )
    _need(
        all(
            type(value) is str and re.fullmatch(r"[1-9][0-9]{0,39}", value) is not None
            for value in (account_uid, main_uid)
        )
    )
    _need(origin in ("https://openapi.okx.com", "https://eea.okx.com"))
    async with factory() as session, session.begin():
        await session.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        )
        await _profile(session, pins)
        row = (
            await session.execute(
                text(
                    "SELECT key_id FROM public.native_live_lifecycle_keys_v1 WHERE environment='live' AND account_uid=:uid"
                ),
                {"uid": account_uid},
            )
        ).one_or_none()
        control = (
            await session.execute(
                text(
                    "SELECT main_uid,armed,emergency_stop,arm_id,arm_expires_at FROM public.native_live_controls_v1 WHERE environment='live' AND account_uid=:uid"
                ),
                {"uid": account_uid},
            )
        ).one()
        _need(tuple(control) == (main_uid, False, True, None, None))
    if path.exists():
        raw, document = _existing_file(path)
    else:
        _need(row is None)
        document = {
            "schema": keys.SCHEMA,
            "environment": "live",
            "account_uid": account_uid,
            "main_uid": main_uid,
            "database_oid": pins.database_oid,
            "owner_oid": pins.owner_oid,
            "reconciler_oid": pins.reconciler_oid,
            "origin": origin,
            "key_id": "0" * 64,
            "key_hex": secrets.token_bytes(32).hex(),
        }
        document["key_id"] = keys.key_identity_v1(document)
        raw = canonical(document)
        _create_file(path, raw)
    expected = {
        "environment": "live",
        "account_uid": account_uid,
        "main_uid": main_uid,
        "database_oid": pins.database_oid,
        "owner_oid": pins.owner_oid,
        "reconciler_oid": pins.reconciler_oid,
        "origin": origin,
    }
    _need(all(document[name] == value for name, value in expected.items()))
    _need(row is None or row[0] == document["key_id"])
    material = bytes.fromhex(document["key_hex"])
    values = {
        **expected,
        "key_id": document["key_id"],
        "material": material,
        "material_sha": sha(material),
    }
    committing = False
    try:
        async with factory() as session, session.begin():
            await session.execute(text("SET LOCAL synchronous_commit='on'"))
            await session.execute(text("SET LOCAL lock_timeout='1000ms'"))
            await session.execute(text("SET LOCAL statement_timeout='10000ms'"))
            await _profile(session, pins)
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": live_account_lock_key_v1(account_uid)},
            )
            _need(_existing_file(path)[0] == raw)
            current = (
                await session.execute(
                    text(
                        "SELECT key_id,key_material FROM public.native_live_lifecycle_keys_v1 WHERE environment='live' AND account_uid=:uid"
                    ),
                    {"uid": account_uid},
                )
            ).one_or_none()
            if current is None:
                await session.execute(
                    text("""INSERT INTO public.native_live_lifecycle_keys_v1
 (environment,account_uid,main_uid,database_oid,owner_oid,reconciler_oid,origin,key_id,key_material,key_material_sha256)
 VALUES(:environment,:account_uid,:main_uid,:database_oid,:owner_oid,:reconciler_oid,:origin,:key_id,:material,:material_sha)"""),
                    values,
                )
            else:
                _need(tuple(current) == (document["key_id"], material))
            committing = True
    except BaseException as exc:
        if committing:
            raise LiveLifecycleKeySetupCommitUnknown(
                "native_live_lifecycle_key_commit_unknown_keep_file_no_rotation"
            ) from None
        if not isinstance(exc, Exception):
            raise
        raise LiveLifecycleKeySetupError(
            "native_live_lifecycle_key_setup_refused_keep_file"
        ) from None
    async with factory() as session, session.begin():
        await session.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        )
        await _profile(session, pins)
        actual = (
            await session.execute(
                text("""SELECT environment,account_uid,main_uid,database_oid::bigint,owner_oid::bigint,
 reconciler_oid::bigint,origin,key_id,key_material,key_material_sha256 FROM public.native_live_lifecycle_keys_v1
 WHERE environment='live' AND account_uid=:uid"""),
                {"uid": account_uid},
            )
        ).one()
        _need(
            tuple(actual)
            == (*expected.values(), document["key_id"], material, sha(material))
        )
    _need(_existing_file(path)[0] == raw)
    return {
        "schema": "ctcc.native_live.lifecycle_key_setup_data.v1",
        "key_id": document["key_id"],
        "journal_key_file": {"path": str(path), "sha256": sha(raw)},
        "execution_authority": False,
    }


async def install_owner_live_lifecycle_key_v1(
    factory, pins, *, path, account_uid, main_uid, origin
):
    """No secret values, private SQL diagnostics or filesystem paths in errors."""
    try:
        return await _install_owner_live_lifecycle_key_v1(
            factory,
            pins,
            path=path,
            account_uid=account_uid,
            main_uid=main_uid,
            origin=origin,
        )
    except (LiveLifecycleKeySetupError, LiveLifecycleKeySetupCommitUnknown):
        raise
    except Exception:  # noqa: BLE001 -- private file/SQL/key failures are deliberately redacted.
        raise LiveLifecycleKeySetupError(
            "native_live_lifecycle_key_setup_unavailable_keep_existing_file"
        ) from None
