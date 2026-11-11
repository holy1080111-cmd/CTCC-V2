from __future__ import annotations

import os
import shutil

import pytest

from app.domain.source_primitives import canonical, decode, sha
from app.exchange.okx import live_account_config_custody_v1 as custody
from app.exchange.okx.live_read_lease_v1 import OkxLivePinnedGetClientV1
from tests.unit.exchange.test_live_account_config_observation_v1 import (
    _http,
    _raw_response,
    _settings,
    _success,
)
from tests.unit.test_native_private_root_inheritance_windows_v2 import (
    _set_protected_directory_dacl,
)

ATTEMPT = "a" * 32


@pytest.fixture
def private_root(tmp_path):
    root = tmp_path / "private-custody"
    root.mkdir(mode=0o700)
    if os.name == "nt":
        _set_protected_directory_dacl(root)
    return root


async def _capture(client, root):
    with client.pinned_get_lease() as lease:
        return await custody.capture_live_account_config_to_private_custody_v1(
            client, lease, private_root=root, attempt_id=ATTEMPT
        )


@pytest.mark.asyncio
async def test_live_config_custody_reopens_and_requires_independent_digest(
    private_root, monkeypatch
):
    calls = []

    def handler(request):
        calls.append(request.method)
        return _success()

    if os.name == "nt":

        def fail_link(*args, **kwargs):
            pytest.fail("Windows custody must not depend on hard-link publication")

        monkeypatch.setattr(os, "link", fail_link)

    async with _http(handler) as http:
        client = OkxLivePinnedGetClientV1(http, settings=_settings())
        receipt = await _capture(client, private_root)
        with pytest.raises(custody.LiveConfigCustodyError):
            await _capture(client, private_root)
    assert calls == ["GET"]
    assert receipt["synthetic_mock"] is True
    for flag in (
        "account_complete",
        "source_authenticity_verified",
        "execution_authority",
    ):
        assert receipt[flag] is False
    assert "1234567" not in str(receipt)
    assert "synthetic-secret" not in str(receipt)
    assert (private_root / ATTEMPT / "body.raw").read_bytes() == (
        b'{"code":"0","msg":"","data":[{"uid":"1234567",'
        b'"mainUid":"1234568","acctLv":"2"}]}'
    )
    assert (
        custody.read_live_account_config_custody_v1(
            private_root,
            attempt_id=ATTEMPT,
            expected_custody_sha256=receipt["custody_sha256"],
        )
        == receipt
    )
    with pytest.raises(custody.LiveConfigCustodyError, match="digest_mismatch"):
        custody.read_live_account_config_custody_v1(
            private_root, attempt_id=ATTEMPT, expected_custody_sha256="0" * 64
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["body", "missing", "extra", "hardlink", "acl", "directory", "clock"]
)
async def test_live_config_custody_refuses_changed_private_attempt(
    private_root, change
):
    async with _http(lambda _: _success()) as http:
        client = OkxLivePinnedGetClientV1(http, settings=_settings())
        receipt = await _capture(client, private_root)
    attempt = private_root / ATTEMPT
    body = attempt / "body.raw"
    expected_digest = receipt["custody_sha256"]
    if change == "body":
        body.write_bytes(body.read_bytes().replace(b'"2"', b'"3"'))
    elif change == "missing":
        (attempt / "observation.json").unlink()
    elif change == "extra":
        (attempt / "extra").write_bytes(b"extra")
    elif change == "hardlink":
        os.link(body, private_root / "alias.raw")
    elif change == "directory":
        moved = private_root / "replaced"
        attempt.rename(moved)
        shutil.copytree(moved, attempt)
    elif change == "clock":
        seal = attempt / "custody.json"
        invalid = decode(seal.read_bytes())
        invalid["raw_readback_complete"] = {"utc_ns": 0, "monotonic_ns": 0}
        raw = canonical(invalid)
        seal.write_bytes(raw)
        expected_digest = sha(raw)
    elif os.name == "nt":
        _set_protected_directory_dacl(body, extra_users=True)
    else:
        body.chmod(0o644)
    with pytest.raises(custody.LiveConfigCustodyError):
        custody.read_live_account_config_custody_v1(
            private_root,
            attempt_id=ATTEMPT,
            expected_custody_sha256=expected_digest,
        )


@pytest.mark.asyncio
async def test_live_config_custody_preserves_late_failed_attempt(
    private_root, monkeypatch
):
    original = custody._publish
    calls = []

    def fail_at_seal(directory, name, raw):
        if name == "custody.json":
            raise OSError(32, "synthetic private failure detail")
        original(directory, name, raw)

    def handler(request):
        calls.append(request.method)
        return _success()

    monkeypatch.setattr(custody, "_publish", fail_at_seal)
    async with _http(handler) as http:
        client = OkxLivePinnedGetClientV1(http, settings=_settings())
        for _ in range(2):
            with pytest.raises(custody.LiveConfigCustodyError, match="capture_failed"):
                await _capture(client, private_root)
    assert calls == ["GET"]
    assert {path.name for path in (private_root / ATTEMPT).iterdir()} == {
        "body.raw",
        "headers.raw",
        "observation.json",
    }
    with pytest.raises(custody.LiveConfigCustodyError, match="inventory_invalid"):
        custody.read_live_account_config_custody_v1(
            private_root, attempt_id=ATTEMPT, expected_custody_sha256="0" * 64
        )


@pytest.mark.asyncio
async def test_live_config_custody_checks_root_privacy_before_request(private_root):
    if os.name == "nt":
        _set_protected_directory_dacl(private_root, extra_users=True)
    else:
        private_root.chmod(0o755)
    async with _http(
        lambda _: pytest.fail("nonprivate root must not issue a GET")
    ) as http:
        client = OkxLivePinnedGetClientV1(http, settings=_settings())
        with pytest.raises(custody.LiveConfigCustodyError, match="capture_failed"):
            await _capture(client, private_root)
    assert not tuple(private_root.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("echo", ["escaped_key", "private_header"])
async def test_live_config_custody_rejects_credential_echo_before_writing(
    private_root, echo
):
    def handler(_):
        if echo == "private_header":
            response = _success()
            response.headers["OK-ACCESS-SIGN"] = "unrelated-value"
            return response
        return _raw_response(
            b'{"code":"0","data":[{"uid":"1234567","mainUid":"1234568",'
            b'"synth\\u0065tic-secret":"echo"}]}'
        )

    async with _http(handler) as http:
        client = OkxLivePinnedGetClientV1(http, settings=_settings())
        with pytest.raises(custody.LiveConfigCustodyError, match="capture_failed"):
            await _capture(client, private_root)
    assert not tuple((private_root / ATTEMPT).iterdir())
