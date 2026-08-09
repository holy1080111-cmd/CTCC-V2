from fastapi.routing import APIRoute

from app.api.routers.okx_live import _http_error, router
from app.api.security import require_ctcc_token
from app.domain.okx_live import (
    OkxLiveAccountSummary,
    OkxLiveBalanceSummary,
    OkxLiveOrderSummary,
    OkxLiveStatus,
    OkxLiveWriteResult,
)
from app.exchange.okx.errors import OkxPrivateApiError


def test_every_okx_live_route_requires_ctcc_token_including_status() -> None:
    routes = [item for item in router.routes if isinstance(item, APIRoute)]

    assert routes
    assert all(item.path.startswith("/api/okx-live/") for item in routes)
    for route in routes:
        dependencies = {item.call for item in route.dependant.dependencies}
        assert require_ctcc_token in dependencies, route.path


def test_live_api_models_exclude_raw_identity_and_credentials() -> None:
    rendered_fields = set().union(
        OkxLiveAccountSummary.model_fields,
        OkxLiveBalanceSummary.model_fields,
        OkxLiveOrderSummary.model_fields,
        OkxLiveStatus.model_fields,
        OkxLiveWriteResult.model_fields,
    )

    assert {
        "uid",
        "main_uid",
        "raw",
        "api_key",
        "api_secret",
        "passphrase",
        "exchange_data",
    }.isdisjoint(rendered_fields)


def test_exchange_http_error_does_not_echo_exchange_message_or_secrets() -> None:
    error = _http_error(
        OkxPrivateApiError(
            "request rejected api_key=live-key secret=live-secret",
            code="51000",
            data=[{"passphrase": "live-passphrase"}],
        )
    )

    rendered = repr(error.detail).lower()
    assert error.status_code == 502
    assert error.detail["exchange_code"] == "51000"
    assert "live-key" not in rendered
    assert "live-secret" not in rendered
    assert "live-passphrase" not in rendered


def test_exchange_http_error_rejects_untrusted_code_text() -> None:
    error = _http_error(
        OkxPrivateApiError(
            "rejected",
            code="secret=live-secret",
        )
    )

    assert error.detail == {
        "message": "okx_live_exchange_request_failed",
        "exchange_code": None,
    }
