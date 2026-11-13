"""Check the configured Demo account endpoint with one signed read."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Sequence

from app.config.settings import get_settings
from app.domain.source_primitives import canonical
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_qualification.account_api_key_policy import (
    classify_demo_api_key_config_fields,
)

_DOCUMENTED_CODES = re.compile(r"50[01][0-9]{2}")
_UID = re.compile(r"[1-9][0-9]{0,39}")
_CATEGORY = {
    "50101": "CREDENTIAL_ENVIRONMENT_MISMATCH",
    "50102": "REQUEST_TIME_EXPIRED",
    "50105": "PASSPHRASE_REJECTED",
    "50109": "PROVIDER_DOMAIN_REJECTED",
    "50110": "IP_NOT_WHITELISTED",
    "50111": "KEY_REJECTED",
    "50113": "SIGNATURE_REJECTED",
    "50119": "KEY_NOT_FOUND",
    "demo_selector_deadline_exceeded": "DEADLINE_EXCEEDED",
    "demo_selector_stream_invalid": "RESPONSE_INCOMPLETE",
    "transport_error": "TRANSPORT_UNAVAILABLE",
    "credentials_missing": "CREDENTIALS_MISSING",
}
_STOPPED = {
    "schema": "ctcc.okx.demo_connection_status.v1",
    "source_verified": False,
    "registered_site_verified": False,
    "account_complete": False,
    "execution_authority": False,
    "order_writes": 0,
}


async def check_demo_connection(
    *, include_key_policy: bool = False
) -> dict[str, object]:
    settings = get_settings()
    if (
        settings.environment == "test"
        or not settings.okx_demo_enabled
        or settings.okx_demo_read_max_retries != 0
    ):
        return {**_STOPPED, "status": "PROFILE_REFUSED"}
    client = OkxDemoPrivateRestClient(settings=settings)
    try:
        with client.pinned_read_session():
            account = await client.account_config_selector_v2()
    except OkxPrivateApiError as error:
        code = error.code
        category = (
            _CATEGORY.get(code, "PRIVATE_READ_REFUSED")
            if type(code) is str
            else "PRIVATE_READ_REFUSED"
        )
        result = {**_STOPPED, "status": category}
        if isinstance(code, str) and _DOCUMENTED_CODES.fullmatch(code):
            result["okx_code"] = code
        return result
    row = account.data[0] if len(account.data) == 1 else {}
    if not all(
        type(value) is str and _UID.fullmatch(value)
        for value in (row.get("uid"), row.get("mainUid"))
    ):
        return {**_STOPPED, "status": "ACCOUNT_IDENTITY_UNAVAILABLE"}
    result = {**_STOPPED, "status": "SIGNED_CONFIG_OBSERVED"}
    if include_key_policy:
        result["api_key_policy"] = classify_demo_api_key_config_fields(
            permissions=row.get("perm"), bound_ips=row.get("ip")
        )
    return result


def main(arguments: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--key-policy",
        action="store_true",
        help="Classify the signed configuration's permissions and bound IPv4 fields.",
    )
    args = parser.parse_args(arguments)
    try:
        result = asyncio.run(check_demo_connection(include_key_policy=args.key_policy))
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):  # noqa: BLE001 -- keep SDK and configuration details private
        result = {**_STOPPED, "status": "CHECK_UNAVAILABLE"}
    print(canonical(result).decode("ascii"))
    return 0 if result["status"] == "SIGNED_CONFIG_OBSERVED" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
