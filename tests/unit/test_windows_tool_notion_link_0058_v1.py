"""Declared synthetic private setup files; no Notion/OKX HTTP or worker."""

import json
import os

import httpx
import pytest
from pydantic import SecretStr

from app.config import windows_tool_profile_0058_v1 as profile
from app.trade_evidence import notion_adapter as notion
from app.trade_evidence import notion_binding as binding
from app.trade_evidence import outbox
from app.trade_evidence.runtime import NotionOutboxRuntime
from scripts import configure_windows_tool_0058_v1 as wizard
from scripts import setup_notion_outbox as setup

TOKEN = SecretStr("declared_synthetic_notion_token_not_a_real_credential")


@pytest.fixture(autouse=True)
def no_remote_or_worker(monkeypatch):
    async def denied(*_args, **_kwargs):
        pytest.fail("setup attempted external HTTP or started a worker")

    monkeypatch.setattr(httpx.AsyncClient, "send", denied)
    monkeypatch.setattr(NotionOutboxRuntime, "start", denied)
    monkeypatch.setattr(
        outbox, "enqueue", lambda *_a, **_k: pytest.fail("setup created an outbox job")
    )


def destination():
    return notion.NotionDestination(
        database_id="1" * 32,
        data_source_id="2" * 32,
        properties=tuple(
            notion.NotionPropertyPin(
                role=role,
                property_id="title" if role == "report_id" else role,
                name=role,
                kind="title" if role == "report_id" else "rich_text",
            )
            for role in notion.ROLES
        ),
    )


def existing(tmp_path):
    path = tmp_path / "existing.destination.json"
    token = tmp_path / "existing.token"
    raw = destination().model_dump_json().encode()
    setup.write_local_record(path, raw)
    binding.save_private_token(token, TOKEN)
    return path, wizard.package.sha(raw), token


def prompts(monkeypatch, path, digest, token):
    answers = iter(("2", digest))
    paths = iter((path, token))
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(wizard, "_path", lambda _: next(paths))
    created = []

    def create(root):
        folder = root / "notion-outbox"
        folder.mkdir()  # This declared fixture exercises configuration only.
        created.append(folder)
        return folder

    monkeypatch.setattr(wizard, "_new_notion_outbox", create)
    return created


@pytest.mark.parametrize("answer", ("", "1"))
def test_default_skip_keeps_flags_off_and_does_not_read_ambient_settings(
    tmp_path, monkeypatch, answer
):
    monkeypatch.setenv("NOTION_OUTBOX_ENABLED", "true")
    monkeypatch.setenv("NOTION_OUTBOX_TOKEN_FILE", "ambient_untrusted_location")
    monkeypatch.setattr("builtins.input", lambda _: answer)
    monkeypatch.setattr(
        wizard, "_path", lambda _: pytest.fail("default discovered private settings")
    )
    monkeypatch.setattr(
        binding, "load_private_token", lambda _: pytest.fail("default opened token")
    )
    values = {"notion_outbox_enabled": True, "submission_outbox_enabled": True}
    wizard._link_existing_notion(values, tmp_path)
    assert values == {
        "notion_outbox_enabled": False,
        "submission_outbox_enabled": False,
    }
    assert list(tmp_path.iterdir()) == []


def test_existing_private_binding_stores_only_three_locators_and_new_empty_root(
    tmp_path, monkeypatch, capsys
):
    path, digest, token = existing(tmp_path)
    created = prompts(monkeypatch, path, digest.upper(), token)
    values = {}
    wizard._link_existing_notion(values, tmp_path)
    assert values["notion_outbox_destination_file"] == str(path)
    assert values["notion_outbox_destination_sha256"] == digest
    assert values["notion_outbox_token_file"] == str(token)
    assert (
        values["notion_outbox_root"]
        == values["submission_outbox_root"]
        == str(tmp_path / "notion-outbox")
    )
    assert (
        values["notion_outbox_enabled"] is values["submission_outbox_enabled"] is False
    )
    assert set(values).issubset(profile._DEMO)
    assert created == [tmp_path / "notion-outbox"] and list(created[0].iterdir()) == []
    assert TOKEN.get_secret_value() not in json.dumps(values)
    assert TOKEN.get_secret_value() not in capsys.readouterr().out


def test_wrong_reviewed_hash_refuses_before_new_queue_or_profile_binding(
    tmp_path, monkeypatch
):
    path, _digest, token = existing(tmp_path)
    created = prompts(monkeypatch, path, "a" * 64, token)
    values = {}
    with pytest.raises(wizard.package.WindowsToolRefused):
        wizard._link_existing_notion(values, tmp_path)
    assert created == [] and "notion_outbox_destination_file" not in values


@pytest.mark.parametrize("file_role", ("destination", "token"))
def test_inherited_or_permissive_existing_file_refuses_without_queue_creation(
    tmp_path, monkeypatch, file_role
):
    path, digest, token = existing(tmp_path)
    legacy = tmp_path / "legacy-file"
    legacy.write_bytes(
        path.read_bytes() if file_role == "destination" else token.read_bytes()
    )
    if os.name != "nt":
        legacy.chmod(0o644)  # Exact synthetic leaf only.
    if file_role == "destination":
        path = legacy
    else:
        token = legacy
    created = prompts(monkeypatch, path, digest, token)
    with pytest.raises(binding.NotionBindingError, match="permissions_invalid"):
        wizard._link_existing_notion({}, tmp_path)
    assert created == []


@pytest.mark.parametrize("fault", ("missing_property", "alias", "wrong_type"))
def test_exact_four_property_metadata_is_required_offline(tmp_path, monkeypatch, fault):
    path, _digest, token = existing(tmp_path)
    data = json.loads(path.read_bytes())
    if fault == "missing_property":
        data["properties"].pop()
    elif fault == "alias":
        data["properties"][1]["property_id"] = data["properties"][0]["property_id"]
    else:
        data["properties"][1]["kind"] = "title"
    other = tmp_path / "invalid.destination.json"
    raw = json.dumps(data).encode()
    setup.write_local_record(other, raw)
    created = prompts(monkeypatch, other, wizard.package.sha(raw), token)
    with pytest.raises(ValueError):
        wizard._link_existing_notion({}, tmp_path)
    assert created == []


def test_existing_queue_is_never_adopted_or_cleared(tmp_path, monkeypatch):
    path, digest, token = existing(tmp_path)
    created = prompts(monkeypatch, path, digest, token)
    folder = tmp_path / "notion-outbox"
    folder.mkdir()
    sentinel = folder / "declared-prior-state"
    sentinel.write_bytes(b"retained fixture")
    with pytest.raises(FileExistsError):
        wizard._link_existing_notion({}, tmp_path)
    assert created == [] and sentinel.read_bytes() == b"retained fixture"
