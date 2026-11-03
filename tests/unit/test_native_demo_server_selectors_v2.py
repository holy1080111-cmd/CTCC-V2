"""Inert public reducer/configuration tests; no signed GET/native owner is mocked into acceptance."""

from dataclasses import fields

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from app.config.settings import Settings
from app.demo_automation import native_server_selectors_v2 as selectors
from app.domain.source_primitives import canonical, decode, sha


def test_exact_uid_and_mainuid_are_independently_preserved_as_selector_data():
    rows = [{"uid": "123", "mainUid": "456", "note": "原始值"}]
    uid, main_uid, parsed = selectors._parse_selector_rows_v2(rows)
    assert (uid, main_uid) == ("123", "456")
    assert parsed == canonical(rows)
    assert decode(parsed, 65536)[0]["note"] == "原始值"
    assert rows == [{"uid": "123", "mainUid": "456", "note": "原始值"}]


def test_main_account_same_uid_is_allowed_without_substituting_fields():
    uid, main_uid, _ = selectors._parse_selector_rows_v2(
        [{"uid": "123", "mainUid": "123"}]
    )
    assert uid == main_uid == "123"


@pytest.mark.parametrize(
    "rows", [[], [{"uid": "123", "mainUid": "456"}] * 2, {}, [None]]
)
def test_missing_or_ambiguous_config_cardinality_rejects(rows):
    with pytest.raises(selectors.NativeDemoCallerReadinessErrorV2, match="cardinality"):
        selectors._parse_selector_rows_v2(rows)


@pytest.mark.parametrize(
    "row",
    [
        {"uid": "123"},
        {"mainUid": "456"},
        {"uid": "123", "mainUid": ""},
        {"uid": "123", "mainUid": None},
        {"uid": 123, "mainUid": "456"},
        {"uid": "123", "mainUid": True},
        {"uid": " 123", "mainUid": "456"},
        {"uid": "0123", "mainUid": "456"},
        {"uid": "123", "mainUid": "4" * 41},
    ],
)
def test_unknown_identity_cannot_be_filled_from_other_field_or_zero(row):
    with pytest.raises(
        selectors.NativeDemoCallerReadinessErrorV2, match="exact_identity_missing"
    ):
        selectors._parse_selector_rows_v2([row])


def test_selector_data_json_response_bound_is_explicit():
    with pytest.raises(
        selectors.NativeDemoCallerReadinessErrorV2, match="response_bound"
    ):
        selectors._parse_selector_rows_v2(
            [{"uid": "123", "mainUid": "456", "note": "x" * 65536}]
        )


def test_fixed_packaged_official_artifact_retains_unknown_registered_site():
    raw = selectors._route_artifact()
    value = decode(raw, 65536)
    assert (
        sha(raw) == "426a0fe117bc756e2c8c2c850f5658134966e8be785d1f2aca46e4ed0e0d7544"
    )
    assert value["configured_rest_origin"] == "https://openapi.okx.com"
    assert value["registered_site"] is None and value["registered_site_proven"] is False
    assert (
        value["registration_claim"] is None
        and value["historical_diagnostic_can_grant_permission"] is False
    )
    assert value["execution_authority"] is False and value["account_complete"] is False


def test_restricted_private_database_settings_are_secret_and_default_missing():
    for name in (
        "okx_demo_native_custody_database_url",
        "okx_demo_native_frame_reader_database_url",
    ):
        default = Settings.model_fields[name].default
        assert type(default) is SecretStr
        assert default.get_secret_value() == ""


def test_database_route_only_normalizes_declared_default_port():
    first = make_url("postgresql+asyncpg://reader:placeholder@localhost/demo")
    second = make_url("postgresql+asyncpg://custody:placeholder@localhost:5432/demo")
    assert selectors._database_route(first) == selectors._database_route(second)
    assert selectors._database_route(first) != selectors._database_route(
        make_url("postgresql+asyncpg://reader:placeholder@elsewhere/demo")
    )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://reader:placeholder@localhost:0/demo",
        "postgresql+asyncpg://reader:placeholder@localhost/demo?options=-crole%3Downer",
        "postgresql+psycopg://reader:placeholder@localhost/demo",
        "postgresql+asyncpg://localhost/demo",
    ],
)
def test_database_role_or_route_unknown_does_not_gain_fallback(url):
    with pytest.raises(selectors.NativeDemoCallerReadinessErrorV2):
        selectors._database_route(make_url(url))


def test_foreign_route_properties_are_not_invoked():
    class Foreign:
        def __getattribute__(self, _name):
            raise AssertionError("foreign route property invoked")

    with pytest.raises(selectors.NativeDemoCallerReadinessErrorV2):
        selectors._database_route(Foreign())


def test_selection_is_not_constructible_and_private_replays_deny_wrong_keys():
    with pytest.raises(
        selectors.NativeDemoCallerReadinessErrorV2, match="not_constructible"
    ):
        selectors.ConfiguredDemoIdentitySelectionV2()
    with pytest.raises(
        selectors.NativeDemoCallerReadinessErrorV2, match="source_key_required"
    ):
        selectors._replay_configured_demo_identity_selection_v2(object(), key=object())
    with pytest.raises(
        selectors.NativeDemoCallerReadinessErrorV2, match="scan_key_required"
    ):
        selectors._configured_demo_selector_scan_tokens_v2(object(), key=object())


def test_native_record_has_only_agreed_eight_source_fields_no_signature():
    assert tuple(
        field.name for field in fields(selectors.ConfiguredDemoIdentityRecordV2)
    ) == (
        "read_session",
        "private_client",
        "current_plan",
        "scope_config",
        "custody_factory",
        "raw_reader_factory",
        "writer_factory",
        "route_evidence",
    )
    assert (
        "signature" not in selectors.ConfiguredDemoRouteEvidenceV2.__dataclass_fields__
    )
    for name in ("registered_site_proven", "account_complete", "execution_authority"):
        assert (
            selectors.ConfiguredDemoRouteEvidenceV2.__dataclass_fields__[name].default
            is False
        )
