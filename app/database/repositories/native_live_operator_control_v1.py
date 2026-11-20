"""Restricted, source-owned manual Live controls with an independent readback.

There is no public positive write method accepting a phrase, DTO or booleans.
Only the canonical coordinator's original, same-task one-use request can enter
this repository. Its operator role cannot write a reservation, intent, outcome
or epoch. A stale/unknown COMMIT is terminal and never retried here.
"""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from types import FunctionType

from sqlalchemy import text

from app.database.repositories import native_live_financial_epoch_v1 as financial
from app.database.repositories import native_live_ledger_v1 as ledger
from app.domain import native_live_operator_control_schema_v1 as schema
from app.domain.native_live_inherited_acl_v1 import (
    require_inherited_live_data_helpers_v1,
)
from app.domain.native_live_intent_v1 import live_account_lock_key_v1
from app.domain.native_live_legacy_safety_v1 import (
    require_legacy_live_negative_fence_v1,
)
from app.domain.source_primitives import canonical, sha
from app.okx_live import trusted_source_bridge_v1 as source

_SOURCE_KEY_V1 = object()
_COORDINATOR = "app.okx_live.owned_qualification_v1"
_PATH = Path(__file__).resolve().parents[2] / "okx_live" / "owned_qualification_v1.py"
_BINDING = {}


class LiveOperatorControlError(ValueError):
    pass


class LiveOperatorCommitUnknown(RuntimeError):
    pass


class LiveOperatorReadbackRequired(RuntimeError):
    pass


def _need(value):
    if value is not True:
        raise LiveOperatorControlError("native_live_operator_control_refused") from None


@dataclass(frozen=True, slots=True, repr=False)
class LiveOperatorRolePinsV1:
    database_oid: int
    owner_oid: int
    operator_oid: int
    operator_name: str
    live_writer_oid: int


@dataclass(frozen=True, slots=True, repr=False)
class LiveOperatorControlRequestV1:
    action: str
    source_owner: object
    financial_owner: object | None
    account_uid: str
    main_uid: str
    credential_session_sha256: str
    source_revision_sha256: str
    policy_sha256: str
    process_sha256: str
    expected_revision: int | None
    arm_id: str | None
    arm_expires_at: datetime | None
    lease_until: datetime
    caps: tuple[Decimal, Decimal, Decimal, Decimal]
    approval_sha256: str | None


@dataclass(frozen=True, slots=True, repr=False)
class LiveOperatorControlReadbackV1:
    row_json: bytes
    row_sha256: str

    @property
    def execution_authority(self):
        return False


def _request(owner):
    from app.trade_qualification.native_continuation_guard_v1 import _source_code

    module = importlib.import_module(_COORDINATOR)
    call = getattr(module, "_consume_owned_live_operator_request_v1", None)
    _need(
        type(call) is FunctionType
        and call.__module__ == _COORDINATOR
        and Path(module.__file__).resolve() == _PATH
        and Path(call.__code__.co_filename).resolve() == _PATH
    )
    raw = _PATH.read_bytes()
    _source_code(call, raw, call.__code__.co_filename)
    binding = (module, call, call.__code__, sha(raw))
    if not _BINDING:
        _BINDING["source"] = binding
    _need(_BINDING.get("source") == binding)
    value = call(owner, key=_SOURCE_KEY_V1)
    _need(type(value) is LiveOperatorControlRequestV1)
    return value


def _factory(factory, pins):
    _need(
        type(pins) is LiveOperatorRolePinsV1
        and pins.live_writer_oid not in (pins.owner_oid, pins.operator_oid)
        and type(pins.live_writer_oid) is int
        and pins.live_writer_oid > 0
    )
    ledger._factory(
        factory,
        ledger.LiveLedgerRolePinsV1(
            pins.database_oid, pins.owner_oid, pins.operator_oid, pins.operator_name
        ),
    )


async def _profile(session, pins):
    row = tuple((await session.execute(ledger._PROFILE)).one())
    _need(
        row[:11]
        == (
            pins.operator_oid,
            pins.operator_name,
            True,
            "none",
            pins.database_oid,
            pins.owner_oid,
            [ledger.schema.HEAD],
            True,
            True,
            True,
            True,
        )
    )
    _need(
        {tuple(item) for item in (await session.execute(ledger._ACCESS)).all()}
        == schema.EFFECTIVE_TABLE_PRIVILEGES
    )
    inherited = await require_inherited_live_data_helpers_v1(
        session, owner_oid=pins.owner_oid
    )
    _need(
        (await session.execute(ledger._ROUTINES)).all()
        == [(name,) for name in sorted(inherited)]
    )
    await ledger._catalog(
        session, owner_oid=pins.owner_oid, actor_oid=pins.operator_oid
    )
    # The operator may not delegate even its narrow privileges.
    _need(
        await session.scalar(
            text("""SELECT NOT EXISTS(
      SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace,
      LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) x
      WHERE n.nspname='public' AND x.grantee=:actor AND x.is_grantable)"""),
            {"actor": pins.operator_oid},
        )
        is True
    )
    return row


def _source(value):
    view = source._replay_owned_live_source_v1(
        value.source_owner, key=source._SOURCE_KEY
    )
    _need(
        (
            value.account_uid,
            value.main_uid,
            value.credential_session_sha256,
            value.source_revision_sha256,
        )
        == (
            view["uid"],
            view["main_uid"],
            view["credential_session_sha256"],
            view["source_revision_sha256"],
        )
    )
    _need(
        view["current_readback"].receipt["no_open_positions_or_pending_orders_observed"]
        is True
    )
    for amount in value.caps:
        _need(type(amount) is Decimal and amount.is_finite() and amount > 0)
    available = Fraction(
        view["current_readback"].usdt_available_equity.available_equity
    )
    trade, portfolio, margin, notional = map(Fraction, value.caps)
    _need(
        trade <= min(Fraction(1, 4), available / 200)
        and trade <= portfolio <= min(Fraction(1, 4), available / 100)
        and margin <= min(Fraction(25), available * Fraction(3, 5))
        and notional <= 25
    )
    return view


async def _local_flat(session, uid):
    _need(
        await session.scalar(
            text("""SELECT NOT EXISTS(
      SELECT 1 FROM public.native_live_reservations_v1 WHERE environment='live'
      AND account_uid=:uid AND state<>'reconciled')
      AND NOT EXISTS(SELECT 1 FROM public.native_live_intents_v1 i
      LEFT JOIN public.native_live_reservations_v1 r USING(reservation_id)
      WHERE i.environment='live' AND i.account_uid=:uid
      AND (r.reservation_id IS NULL OR r.state<>'reconciled'))"""),
            {"uid": uid},
        )
        is True
    )


def _financial(value):
    facts = financial._replay_owned_live_financial_epoch_v1(
        value.financial_owner, key=financial._KEY
    )
    _need(
        facts.missing_obligations == ()
        and (
            facts.account_uid,
            facts.main_uid,
            facts.credential_session_sha256,
            facts.source_revision_sha256,
        )
        == (
            value.account_uid,
            value.main_uid,
            value.credential_session_sha256,
            value.source_revision_sha256,
        )
    )


def _row_json(row):
    return canonical(
        {
            name: (
                value.isoformat()
                if type(value) is datetime
                else format(value, "f")
                if type(value) is Decimal
                else value
            )
            for name, value in row.items()
        }
    )


def _change(row, request, *, now, writer_oid):
    """Exact full-row CAS computation; no defaults for missing source fields."""
    _need(
        type(now) is datetime
        and now.tzinfo is UTC
        and type(request) is LiveOperatorControlRequestV1
        and request.action in ("prepare", "clear_stop", "arm", "refresh", "stop")
    )
    if row is None:
        _need(request.action == "prepare" and request.expected_revision is None)
        return {
            "environment": "live",
            "account_uid": request.account_uid,
            "main_uid": request.main_uid,
            "credential_session_sha256": request.credential_session_sha256,
            "live_writer_oid": writer_oid,
            "revision": 1,
            "source_revision_sha256": request.source_revision_sha256,
            "policy_sha256": request.policy_sha256,
            "process_sha256": request.process_sha256,
            "emergency_stop": True,
            "armed": False,
            "arm_id": None,
            "arm_expires_at": None,
            "lease_until": request.lease_until,
            "per_trade_risk_cap": request.caps[0],
            "portfolio_risk_cap": request.caps[1],
            "margin_cap": request.caps[2],
            "micro_notional_cap": request.caps[3],
            "updated_at": now,
        }
    _need(
        type(row) is dict
        and row["environment"] == "live"
        and row["account_uid"] == request.account_uid
        and row["main_uid"] == request.main_uid
        and row["live_writer_oid"] == writer_oid
        and row["revision"] == request.expected_revision
        and row["updated_at"] <= now
    )
    changed = dict(row)
    if request.action == "stop":
        changed.update(
            armed=False, arm_id=None, arm_expires_at=None, emergency_stop=True
        )
    else:
        _need(request.lease_until > now)
        if request.action == "prepare":
            _need(row["armed"] is False and row["emergency_stop"] is True)
            changed.update(
                credential_session_sha256=request.credential_session_sha256,
                source_revision_sha256=request.source_revision_sha256,
                process_sha256=request.process_sha256,
                policy_sha256=request.policy_sha256,
                lease_until=request.lease_until,
                per_trade_risk_cap=min(row["per_trade_risk_cap"], request.caps[0]),
                portfolio_risk_cap=min(row["portfolio_risk_cap"], request.caps[1]),
                margin_cap=min(row["margin_cap"], request.caps[2]),
                micro_notional_cap=min(row["micro_notional_cap"], request.caps[3]),
            )
        else:
            _need(
                all(
                    row[name] == getattr(request, name)
                    for name in (
                        "credential_session_sha256",
                        "process_sha256",
                        "policy_sha256",
                    )
                )
            )
            _need(
                tuple(
                    row[name]
                    for name in (
                        "per_trade_risk_cap",
                        "portfolio_risk_cap",
                        "margin_cap",
                        "micro_notional_cap",
                    )
                )
                == request.caps
            )
            _need(
                type(request.approval_sha256) is str
                and len(request.approval_sha256) == 64
            )
            if request.action == "clear_stop":
                _need(
                    row["armed"] is False
                    and row["emergency_stop"] is True
                    and row["source_revision_sha256"] == request.source_revision_sha256
                )
                changed["emergency_stop"] = False
            elif request.action == "arm":
                _need(
                    row["armed"] is False
                    and row["emergency_stop"] is False
                    and row["source_revision_sha256"] == request.source_revision_sha256
                    and type(request.arm_expires_at) is datetime
                    and request.arm_expires_at.tzinfo is UTC
                    and now < request.arm_expires_at <= now + timedelta(seconds=30)
                    and request.arm_expires_at <= row["lease_until"]
                    and type(request.arm_id) is str
                    and len(request.arm_id) == 64
                )
                changed.update(
                    armed=True,
                    arm_id=request.arm_id,
                    arm_expires_at=request.arm_expires_at,
                )
            else:
                _need(
                    row["armed"] is True
                    and row["emergency_stop"] is False
                    and row["arm_id"] == request.arm_id
                    and row["arm_expires_at"] == request.arm_expires_at
                    and now < row["arm_expires_at"] <= row["lease_until"]
                    and request.lease_until == row["lease_until"]
                )
                changed["source_revision_sha256"] = request.source_revision_sha256
    changed.update(revision=row["revision"] + 1, updated_at=now)
    return changed


class NativeLiveOperatorControlRepositoryV1:
    def __init__(self, factory, pins):
        _factory(factory, pins)
        self.factory, self.pins = factory, pins

    async def inhibit_configured_account_v1(self, *, account_uid, main_uid):
        """Negative-only startup operation; no source, insertion or Arm API.

        Existing exposure/latches are retained. The original global exclusion
        is acquired without interpreting an uncertain legacy account as flat.
        """
        from pydantic import TypeAdapter

        from app.domain.native_live_intent_v1 import Uid, live_account_lock_key_v1

        account_uid = TypeAdapter(Uid).validate_python(account_uid, strict=True)
        main_uid = TypeAdapter(Uid).validate_python(main_uid, strict=True)
        attempted = committed = False
        expected = None
        try:
            async with self.factory() as session:
                transaction = await session.begin()
                try:
                    for statement in ledger._SETTINGS:
                        await session.execute(text(statement))
                    first = await _profile(session, self.pins)
                    await session.execute(
                        text("SELECT pg_advisory_xact_lock(:key)"),
                        {"key": live_account_lock_key_v1(account_uid)},
                    )
                    _need(
                        await session.scalar(
                            text("SELECT pg_try_advisory_xact_lock(1680010)")
                        )
                        is True
                    )
                    rows = (
                        (
                            await session.execute(
                                text(
                                    "SELECT * FROM public.native_live_controls_v1 "
                                    "WHERE environment='live' AND account_uid=:uid FOR UPDATE"
                                ),
                                {"uid": account_uid},
                            )
                        )
                        .mappings()
                        .all()
                    )
                    _need(len(rows) <= 1)
                    if rows:
                        row = dict(rows[0])
                        _need(
                            row["main_uid"] == main_uid
                            and row["live_writer_oid"] == self.pins.live_writer_oid
                        )
                        now = await session.scalar(text("SELECT clock_timestamp()"))
                        _need(now.tzinfo is UTC and now >= row["updated_at"])
                        changed = row | {
                            "armed": False,
                            "arm_id": None,
                            "arm_expires_at": None,
                            "emergency_stop": True,
                            "revision": row["revision"] + 1,
                            "updated_at": now,
                        }
                        result = await session.execute(
                            text(
                                "UPDATE public.native_live_controls_v1 SET "
                                "armed=false,arm_id=NULL,arm_expires_at=NULL,emergency_stop=true,revision=:after,updated_at=:now "
                                "WHERE environment='live' AND account_uid=:uid AND revision=:before"
                            ),
                            {
                                "after": changed["revision"],
                                "now": now,
                                "uid": account_uid,
                                "before": row["revision"],
                            },
                        )
                        _need(result.rowcount == 1)
                        expected = _row_json(changed)
                    last = await _profile(session, self.pins)
                    _need(
                        last[:12] == first[:12]
                        and session.get_transaction() is transaction
                    )
                    attempted = True
                    await transaction.commit()
                    committed = True
                except BaseException:
                    if not attempted:
                        await transaction.rollback()
                    raise
            observed = await self.read_control_data_v1(account_uid)
            _need(
                (observed is None and expected is None)
                or (observed is not None and observed.row_json == expected)
            )
            return observed
        except BaseException:  # noqa: BLE001 -- uncertain negative COMMIT cannot imply readiness.
            if committed:
                raise LiveOperatorReadbackRequired(
                    "native_live_operator_committed_readback_required"
                ) from None
            if attempted:
                raise LiveOperatorCommitUnknown(
                    "native_live_operator_commit_unknown_do_not_retry"
                ) from None
            raise LiveOperatorControlError(
                "native_live_operator_control_refused"
            ) from None

    async def read_control_data_v1(self, account_uid):
        from pydantic import TypeAdapter

        from app.domain.native_live_intent_v1 import Uid

        account_uid = TypeAdapter(Uid).validate_python(account_uid, strict=True)
        async with self.factory() as session, session.begin():
            for sql in (
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
                *ledger._SETTINGS[1:],
            ):
                await session.execute(text(sql))
            await _profile(session, self.pins)
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM public.native_live_controls_v1 "
                            "WHERE environment='live' AND account_uid=:uid"
                        ),
                        {"uid": account_uid},
                    )
                )
                .mappings()
                .all()
            )
            _need(len(rows) <= 1)
            return (
                None
                if not rows
                else LiveOperatorControlReadbackV1(
                    _row_json(dict(rows[0])), sha(_row_json(dict(rows[0])))
                )
            )

    async def apply_owned_request_v1(self, owner):
        request = _request(owner)  # Consumes before the first SQL await.
        attempted = committed = False
        try:
            if request.action != "stop":
                _source(request)
            async with asyncio.timeout(15), self.factory() as session:
                transaction = await session.begin()
                try:
                    for sql in ledger._SETTINGS:
                        await session.execute(text(sql))
                    first = await _profile(session, self.pins)
                    await session.execute(
                        text("SELECT pg_advisory_xact_lock(:key)"),
                        {"key": live_account_lock_key_v1(request.account_uid)},
                    )
                    if request.action != "stop":
                        await require_legacy_live_negative_fence_v1(session)
                        await _local_flat(session, request.account_uid)
                    rows = (
                        (
                            await session.execute(
                                text(
                                    "SELECT * FROM public.native_live_controls_v1 "
                                    "WHERE environment='live' AND account_uid=:uid FOR UPDATE"
                                ),
                                {"uid": request.account_uid},
                            )
                        )
                        .mappings()
                        .all()
                    )
                    _need(len(rows) <= 1)
                    previous = dict(rows[0]) if rows else None
                    now = await session.scalar(text("SELECT clock_timestamp()"))
                    changed = _change(
                        previous, request, now=now, writer_oid=self.pins.live_writer_oid
                    )
                    if request.action in ("clear_stop", "arm", "refresh"):
                        _financial(request)
                    if request.action != "stop":
                        _source(request)
                    if previous is None:
                        names = tuple(changed)
                        await session.execute(
                            text(
                                "INSERT INTO public.native_live_controls_v1 ("
                                + ",".join(names)
                                + ") VALUES ("
                                + ",".join(":" + n for n in names)
                                + ")"
                            ),
                            changed,
                        )
                    else:
                        names = tuple(
                            n
                            for n in changed
                            if n not in ("environment", "account_uid")
                        )
                        result = await session.execute(
                            text(
                                "UPDATE public.native_live_controls_v1 SET "
                                + ",".join(n + "=:" + n for n in names)
                                + " WHERE environment='live' AND account_uid=:account_uid AND revision=:before"
                            ),
                            changed | {"before": previous["revision"]},
                        )
                        _need(result.rowcount == 1)
                    last = await _profile(session, self.pins)
                    _need(
                        last[:12] == first[:12]
                        and last[12] is not None
                        and session.get_transaction() is transaction
                    )
                    if request.action != "stop":
                        _source(request)
                    attempted = True
                    await transaction.commit()
                    committed = True
                except BaseException:
                    if not attempted:
                        await transaction.rollback()
                    raise
            expected = _row_json(changed)
            async with self.factory() as session, session.begin():
                for sql in (
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
                    *ledger._SETTINGS[1:],
                ):
                    await session.execute(text(sql))
                await _profile(session, self.pins)
                actual = dict(
                    (
                        await session.execute(
                            text(
                                "SELECT * FROM public.native_live_controls_v1 "
                                "WHERE environment='live' AND account_uid=:uid"
                            ),
                            {"uid": request.account_uid},
                        )
                    )
                    .mappings()
                    .one()
                )
                _need(_row_json(actual) == expected)
            return LiveOperatorControlReadbackV1(expected, sha(expected))
        except BaseException:  # noqa: BLE001 -- uncertain COMMIT/cancellation is terminal.
            if committed:
                raise LiveOperatorReadbackRequired(
                    "native_live_operator_committed_readback_required"
                ) from None
            if attempted:
                raise LiveOperatorCommitUnknown(
                    "native_live_operator_commit_unknown_do_not_retry"
                ) from None
            raise LiveOperatorControlError(
                "native_live_operator_control_refused"
            ) from None
