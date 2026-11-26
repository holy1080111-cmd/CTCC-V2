"""Coordinate a held cold PostgreSQL handoff before Paper can reopen."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass


class HandoffRefused(ValueError):
    """The held operation cannot be safely advanced."""


@dataclass(frozen=True, slots=True)
class HandoffOutcome:
    status: str
    intent_sha256: str
    terminal_sha256: str | None = None


def _need(value):
    if value is not True:
        raise HandoffRefused("paper_wal_handoff_refused") from None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _names(operation_id):
    _need(
        type(operation_id) is str
        and re.fullmatch(r"[0-9a-f]{32}", operation_id) is not None
    )
    prefix = f"wal-handoff-{operation_id}".encode("ascii")
    return (
        prefix + b".intent.json",
        prefix + b".exchange.json",
        prefix + b".completed.json",
    )


def _private_regular(info):
    return stat.S_ISREG(info.st_mode) and (
        info.st_uid,
        info.st_gid,
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
    ) == (0, 0, 0o600, 1)


def _fingerprint(info):
    return (
        *_identity(info),
        *[
            getattr(info, field)
            for field in (
                "st_mode",
                "st_uid",
                "st_gid",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        ],
    )


def _identity(info):
    return info.st_dev, info.st_ino


@contextmanager
def _operator_lock(parent_fd):
    parent = os.fstat(parent_fd)
    _need(os.geteuid() == 0 and stat.S_ISDIR(parent.st_mode))
    _need((parent.st_uid, parent.st_gid, stat.S_IMODE(parent.st_mode)) == (0, 0, 0o700))
    fd = os.open(
        b"active.lock",
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
        dir_fd=parent_fd,
    )
    try:
        info = os.fstat(fd)
        _need(_private_regular(info) and info.st_dev == parent.st_dev)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _need(
            _identity(os.stat(b"active.lock", dir_fd=parent_fd, follow_symlinks=False))
            == _identity(info)
        )
        yield fd
    finally:
        os.close(fd)


def _read(parent_fd, name, *, missing_ok=False):
    try:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        _need(_private_regular(before) and 0 < before.st_size <= 32768)
        raw = stream.read(32769)
        _need(
            len(raw) == before.st_size
            and _fingerprint(before) == _fingerprint(os.fstat(stream.fileno()))
        )
    _need(
        _fingerprint(before)
        == _fingerprint(os.stat(name, dir_fd=parent_fd, follow_symlinks=False))
    )
    return raw


def _publish(parent_fd, name, data):
    _need(type(data) is bytes and 0 < len(data) <= 32768)
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=parent_fd,
    )
    with os.fdopen(fd, "wb") as stream:
        _need(_private_regular(os.fstat(stream.fileno())))
        _need(stream.write(data) == len(data))
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(parent_fd)
    _need(_read(parent_fd, name) == data)


def _plan(exchange, anchor, target, operation_id):
    return exchange.ExchangePlan(
        descriptor_sha256=anchor.descriptor_sha256,
        original_name=b"data",
        recovered_name=f"wal-recovered-{operation_id}".encode("ascii"),
        original=exchange.DirectoryIdentity(target.origin_dev, target.origin_ino),
        recovered=exchange.DirectoryIdentity(
            target.candidate_dev, target.candidate_ino
        ),
        original_tree_sha256=target.origin_sha256,
        recovered_tree_sha256=target.candidate_sha256,
    )


@contextmanager
def _held_roles(parent_fd, plan, target):
    roles = {
        (target.origin_dev, target.origin_ino): "origin",
        (target.candidate_dev, target.candidate_ino): "candidate",
    }
    _need(len(roles) == 2)
    files = {}
    with ExitStack() as resources:
        for name in (plan.original_name, plan.recovered_name):
            fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
            resources.callback(os.close, fd)
            role = roles.get(_identity(os.fstat(fd)))
            _need(role is not None and role not in files)
            files[role] = fd
        yield files["origin"], files["candidate"]


def _verified_pair(copy_tool, original_fd, recovered_fd, target):
    origin = copy_tool.inspect_cold_pgdata(original_fd)
    candidate = copy_tool.inspect_cold_pgdata(recovered_fd)
    _need(
        (origin.device, origin.inode, origin.tree_sha256)
        == (target.origin_dev, target.origin_ino, target.origin_sha256)
    )
    _need(
        (candidate.device, candidate.inode, candidate.tree_sha256)
        == (target.candidate_dev, target.candidate_ino, target.candidate_sha256)
    )
    return origin, candidate


def _reserve(
    parent_fd,
    original_fd,
    recovered_fd,
    *,
    names,
    plan,
    anchor,
    target,
    operation_id,
    journal,
    exchange,
    copy_tool,
):
    _need(all(_read(parent_fd, name, missing_ok=True) is None for name in names))
    slots = set(os.listdir(parent_fd))
    for slot in slots:
        other = re.fullmatch(
            r"wal-handoff-([0-9a-f]{32})\.(?:intent|exchange|completed)\.json", slot
        )
        if other is None:
            continue
        previous = _names(other[1])
        _need(all(name.decode("ascii") in slots for name in previous))
        if slot.endswith(".intent.json"):
            raw = _read(parent_fd, previous[0])
            prior_anchor = journal.WalHandoffAnchor(**json.loads(raw)["anchor"])
            terminal = _read(parent_fd, previous[2])
            prior = journal.inspect_handoff(raw, terminal, anchor=prior_anchor)
            _need(prior.phase == journal.COMPLETED and prior.operation_id == other[1])
            _need(
                prior.anchor.sandbox == anchor.sandbox
                and prior.anchor.counter < anchor.counter
            )
            witness = exchange.verify_witness(
                parent_fd,
                plan=_plan(exchange, prior.anchor, prior.target, prior.operation_id),
                witness_name=previous[1],
            )
            _need(json.loads(terminal)["observation"]["publication_sha256"] == witness)
    _need(
        exchange.inspect_exchange(parent_fd, original_fd, recovered_fd, plan=plan)
        is exchange.ExchangeState.PRE
    )
    _verified_pair(copy_tool, original_fd, recovered_fd, target)
    raw = journal.make_handoff_intent(anchor, target, operation_id=operation_id)
    _publish(parent_fd, names[0], raw)
    return raw


def _observations(
    parent_fd,
    original_fd,
    recovered_fd,
    *,
    state,
    names,
    plan,
    journal,
    exchange,
    copy_tool,
):
    target, anchor = state.target, state.anchor
    witness = exchange.verify_witness(parent_fd, plan=plan, witness_name=names[1])
    os.fsync(parent_fd)
    challenge = secrets.token_hex(32)
    frames = []
    first_pair = None
    for index in (1, 2):
        _need(
            exchange.inspect_exchange(parent_fd, original_fd, recovered_fd, plan=plan)
            is exchange.ExchangeState.POST
        )
        pair = _verified_pair(copy_tool, original_fd, recovered_fd, target)
        if first_pair is None:
            first_pair = pair
        else:
            _need(pair == first_pair)
        _need(
            exchange.inspect_exchange(parent_fd, original_fd, recovered_fd, plan=plan)
            is exchange.ExchangeState.POST
        )
        _need(
            exchange.verify_witness(parent_fd, plan=plan, witness_name=names[1])
            == witness
        )
        origin, recovered = pair
        frame = {
            "schema": journal.READBACK_SCHEMA,
            "operation_id": state.operation_id,
            "intent_sha256": state.intent_sha256,
            "challenge": challenge,
            "probe_index": index,
            "publication_sha256": witness,
            "sandbox": anchor.sandbox,
            "system_sha256": anchor.system_sha256,
            "active_data_sha256": recovered.tree_sha256,
            "active_dev": recovered.device,
            "active_ino": recovered.inode,
            "preserved_origin_sha256": origin.tree_sha256,
            "preserved_origin_dev": origin.device,
            "preserved_origin_ino": origin.inode,
            "cluster_state": "shut down",
            "execution_authority": False,
        }
        frame["receipt_sha256"] = _sha(
            json.dumps(frame, sort_keys=True, separators=(",", ":")).encode("ascii")
        )
        frames.append(frame)
    return challenge, {
        "schema": journal.OBSERVATION_SCHEMA,
        "operation_id": state.operation_id,
        "intent_sha256": state.intent_sha256,
        "challenge": challenge,
        "publication_sha256": witness,
        "publication_synced": True,
        "first": frames[0],
        "second": frames[1],
        "execution_authority": False,
    }


def _finish(
    parent_fd,
    original_fd,
    recovered_fd,
    *,
    intent,
    state,
    names,
    plan,
    journal,
    exchange,
    copy_tool,
):
    challenge, observation = _observations(
        parent_fd,
        original_fd,
        recovered_fd,
        state=state,
        names=names,
        plan=plan,
        journal=journal,
        exchange=exchange,
        copy_tool=copy_tool,
    )
    terminal = journal.make_handoff_terminal(
        state, challenge=challenge, observation=observation
    )
    _publish(parent_fd, names[2], terminal)
    _need(_read(parent_fd, names[0]) == intent)
    observed = journal.inspect_handoff(
        intent, _read(parent_fd, names[2]), anchor=state.anchor
    )
    _need(observed.phase == "COMPLETED" and observed.terminal_sha256 == _sha(terminal))
    return HandoffOutcome("COMPLETED", observed.intent_sha256, observed.terminal_sha256)


def publish_new(
    parent_fd, *, anchor, target, operation_id, journal, exchange, copy_tool
):
    """Publish only from a cleared slot and a newly verified stopped candidate."""
    names = _names(operation_id)
    plan = _plan(exchange, anchor, target, operation_id)
    with (
        _operator_lock(parent_fd) as lock_fd,
        _held_roles(parent_fd, plan, target) as (original_fd, recovered_fd),
    ):
        raw = _reserve(
            parent_fd,
            original_fd,
            recovered_fd,
            names=names,
            plan=plan,
            anchor=anchor,
            target=target,
            operation_id=operation_id,
            journal=journal,
            exchange=exchange,
            copy_tool=copy_tool,
        )
        state = journal.inspect_handoff(raw, None, anchor=anchor)
        result = exchange.exchange_pgdata(
            parent_fd,
            original_fd,
            recovered_fd,
            plan=plan,
            witness_name=names[1],
            lock_fd=lock_fd,
        )
        _need(
            result.state is exchange.ExchangeState.POST
            and result.parent_fsynced is True
        )
        return _finish(
            parent_fd,
            original_fd,
            recovered_fd,
            intent=raw,
            state=state,
            names=names,
            plan=plan,
            journal=journal,
            exchange=exchange,
            copy_tool=copy_tool,
        )


def reconcile(parent_fd, *, anchor, operation_id, journal, exchange, copy_tool):
    """Reopen and verify the observed position without issuing an exchange."""
    names = _names(operation_id)
    with _operator_lock(parent_fd):
        intent = _read(parent_fd, names[0])
        terminal = _read(parent_fd, names[2], missing_ok=True)
        state = journal.inspect_handoff(intent, terminal, anchor=anchor)
        _need(state.operation_id == operation_id)
        plan = _plan(exchange, anchor, state.target, operation_id)
        with _held_roles(parent_fd, plan, state.target) as (original_fd, recovered_fd):
            observed = exchange.inspect_exchange(
                parent_fd, original_fd, recovered_fd, plan=plan
            )
            _verified_pair(copy_tool, original_fd, recovered_fd, state.target)
            if observed is exchange.ExchangeState.PRE:
                _need(terminal is None)
                if _read(parent_fd, names[1], missing_ok=True) is not None:
                    exchange.verify_witness(parent_fd, plan=plan, witness_name=names[1])
                return HandoffOutcome("PENDING_BEFORE_EXCHANGE", state.intent_sha256)
            _need(observed is exchange.ExchangeState.POST)
            witness = exchange.verify_witness(
                parent_fd, plan=plan, witness_name=names[1]
            )
            if terminal is not None:
                _need(state.phase == journal.COMPLETED)
                _need(
                    json.loads(terminal)["observation"]["publication_sha256"] == witness
                )
                os.fsync(parent_fd)
                return HandoffOutcome(
                    "ALREADY_COMPLETED", state.intent_sha256, state.terminal_sha256
                )
            return _finish(
                parent_fd,
                original_fd,
                recovered_fd,
                intent=intent,
                state=state,
                names=names,
                plan=plan,
                journal=journal,
                exchange=exchange,
                copy_tool=copy_tool,
            )
