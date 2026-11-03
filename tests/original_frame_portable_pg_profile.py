"""Explicit synthetic PG17 lab profile; never production/source authority.

Only the current one-shot supervisor provides this binding. It must name a
fresh user-only lab, literal loopback, dedicated port and actual SQL identity.
The original Docker fixture profile remains a separate unchanged branch.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

BINDING_ENV = "CTCC_ORIGINAL_FRAME_PG_BINDING"
BINDING_SHA_ENV = "CTCC_ORIGINAL_FRAME_PG_BINDING_SHA256"
PROFILE = "ctcc.original_frame.synthetic_portable_pg17.v1"


class PortableLabProfileError(ValueError):
    """Fixed nonsecret diagnostics; never include URL/password or SQL rows."""


def _deny():
    raise PortableLabProfileError(
        "original_frame_portable_lab_binding_invalid"
    ) from None


def portable_profile(source_root):
    encoded = os.environ.get(BINDING_ENV)
    expected = os.environ.get(BINDING_SHA_ENV)
    if encoded is None and expected is None:
        return None
    if (
        type(encoded) is not str
        or len(encoded) > 16384
        or re.fullmatch(r"[a-f0-9]{64}", expected or "") is None
    ):
        _deny()
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not 0 < len(raw) <= 8192 or hashlib.sha256(raw).hexdigest() != expected:
            _deny()

        def pairs(items):
            result = {}
            for key, item in items:
                if key in result:
                    _deny()
                result[key] = item
            return result

        value = json.loads(raw, object_pairs_hook=pairs)
        keys = {
            "schema",
            "nonce",
            "host",
            "port",
            "role",
            "database",
            "data_directory",
            "system_identifier",
            "server_version_num",
            "postmaster_started_at",
            "source_root",
            "source_checkpoint_sha256",
        }
        if type(value) is not dict or set(value) != keys or value["schema"] != PROFILE:
            _deny()
        if any(
            type(value[name]) is not str
            for name in keys - {"port", "server_version_num"}
        ):
            _deny()
        if (
            re.fullmatch(r"[a-f0-9]{32}", value["nonce"]) is None
            or value["host"] != "127.0.0.1"
            or value["role"] != "ctcc"
            or value["database"] != "ctcc"
            or type(value["port"]) is not int
            or not 20000 <= value["port"] <= 60000
            or type(value["server_version_num"]) is not int
            or value["server_version_num"] != 170011
        ):
            _deny()
        if (
            re.fullmatch(r"[1-9][0-9]{0,19}", value["system_identifier"]) is None
            or not 0 < int(value["system_identifier"]) < 2**64
            or re.fullmatch(r"[a-f0-9]{64}", value["source_checkpoint_sha256"]) is None
        ):
            _deny()
        root = Path(source_root).resolve(strict=True)
        if Path(value["source_root"]).resolve(strict=True) != root:
            _deny()
        data = Path(value["data_directory"])
        # New fixed lab namespace, never the old CTCC/lab-private trees.
        if (
            not data.is_absolute()
            or data.name != "data"
            or data.parent.name != value["nonce"]
            or data.parent.parent.name != "CTCCOriginalFramePG17Labs"
        ):
            _deny()
        for part in (data, *data.parents):
            if part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & 0x400:
                _deny()
        return value
    except PortableLabProfileError:
        raise
    except Exception:  # noqa: BLE001 -- refuse without credential-bearing exception text
        _deny()


def verify_portable_url(url, profile, *, database="ctcc"):
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host != profile["host"]
        or url.port != profile["port"]
        or url.username != profile["role"]
        or url.database != database
        or url.query
        or re.fullmatch(r"[a-f0-9]{64}", url.password or "") is None
    ):
        _deny()


def parse_portable_url(raw):
    try:
        return make_url(raw)
    except Exception:  # noqa: BLE001 -- refuse without credential-bearing exception text
        # URL parser errors must not echo a synthetic password-bearing DSN.
        _deny()


async def verify_portable_server(connection, url, profile):
    verify_portable_url(url, profile)
    row = (
        await connection.execute(
            text("""
      SELECT current_database() AS database,current_user AS username,session_user AS session,
        current_setting('data_directory') AS data_directory,
        (pg_control_system()).system_identifier::text AS system_identifier,
        current_setting('server_version_num')::int AS version,
        host(inet_server_addr()) AS address,inet_server_port() AS port,
        pg_postmaster_start_time() AS started
    """)
        )
    ).one()
    if (
        row.database != "ctcc"
        or row.username != profile["role"]
        or row.session != profile["role"]
        or Path(row.data_directory).resolve(strict=True)
        != Path(profile["data_directory"]).resolve(strict=True)
        or row.system_identifier != profile["system_identifier"]
        or row.version != profile["server_version_num"]
        or row.address != profile["host"]
        or row.port != profile["port"]
        or row.started.isoformat() != profile["postmaster_started_at"]
    ):
        _deny()


async def create_portable_reader_role(connection, role):
    """New per-test credentials only; never copy the lab admin or host key."""
    if re.fullmatch(r"ctcc_raw_frame_[a-f0-9]{32}", role) is None:
        _deny()
    password = secrets.token_hex(32)
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, 4096)
    client = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    server = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def encode(value):
        return base64.b64encode(value).decode("ascii")

    verifier = (
        "SCRAM-SHA-256$4096:"
        + encode(salt)
        + "$"
        + encode(hashlib.sha256(client).digest())
        + ":"
        + encode(server)
    )
    # Utility DDL has no bind parameters; only generated safe role + SCRAM
    # verifier enter this SQL. The plaintext password is never SQL/log output.
    try:
        await connection.exec_driver_sql(
            'CREATE ROLE "'
            + role
            + '" LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '
            + "'"
            + verifier
            + "'"
        )
    except Exception:  # noqa: BLE001 -- refuse without credential-bearing exception text
        # SQLAlchemy's raw DDL exception embeds the SCRAM verifier SQL. Never
        # permit that exception/context into pytest/JUnit, even in a private lab.
        _deny()
    return password
