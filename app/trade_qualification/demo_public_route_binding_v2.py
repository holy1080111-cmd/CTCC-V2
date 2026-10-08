"""DENY-only link from one native Demo account observation to reviewed routes.

This does not authorize public network IO. In particular, a signed account
request on one private origin does not prove the account's registration region
or authenticate a later public HTTP response or WebSocket frame.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.trade_qualification import account_native_runtime as native_account
from app.trade_qualification import demo_public_origin as public_origin


class DemoPublicRouteBindingError(ValueError):
    """Fixed local code, without account or credential details."""


@dataclass(frozen=True, slots=True, repr=False)
class DemoPublicRouteBindingDiagnostic:
    """Observed private route matched to policy, with no public authority."""

    reviewed_route: public_origin.ReviewedDemoPublicRoute
    observed_private_origin: str
    observed_tls_hostname: str
    account_uid: str
    account_main_uid: str
    session_binding_id: str
    claimed_registration_evidence_sha256: str
    account_plan_sha256: str
    account_packet_sha256: str
    native_proof_sha256: str
    native_readback_sha256: str
    observed_at: datetime
    expires_at: datetime
    admission: str = field(default="DENY", init=False)
    registration_region_verified: bool = field(default=False, init=False)
    public_source_authenticity_verified: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


def consume_native_demo_route_binding(
    diagnostic: native_account.InitialNativeAccountDiagnostic,
    session: object,
) -> DemoPublicRouteBindingDiagnostic:
    """Consume the native one-use lease, then match its origin to fixed policy.

    No caller-supplied region or route is accepted. The native lease is burned
    even if the observed origin cannot be matched; callers cannot retry it with
    a different declaration. The returned object is a diagnostic, never an IO
    token or proof of the account's registration region.
    """
    try:
        observed = native_account._consume_demo_account_origin(diagnostic, session)
        if (
            type(observed) is not native_account._ObservedDemoAccountOrigin
            or observed.signed_account_config_observed is not True
            or observed.simulated_trading_header != "1"
            or observed.registration_region_verified is not False
            or observed.public_source_authenticity_verified is not False
            or observed.execution_authority is not False
            or observed.admission != "DENY"
        ):
            raise ValueError
        matching = tuple(
            route
            for region in ("global", "us_au", "eea")
            if (route := public_origin.reviewed_demo_public_route(region)).rest_origin
            == observed.private_origin
        )
        if (
            len(matching) != 1
            or observed.tls_hostname != matching[0].rest_hostname
            or observed.uid != session._plan.expected_uid
            or observed.main_uid != session._plan.expected_main_uid
            or observed.session_binding_id != session._plan.session_binding_id
            or observed.claimed_registration_evidence_sha256
            != session._plan.registration_evidence_sha256
        ):
            raise ValueError
        return DemoPublicRouteBindingDiagnostic(
            reviewed_route=matching[0],
            observed_private_origin=observed.private_origin,
            observed_tls_hostname=observed.tls_hostname,
            account_uid=observed.uid,
            account_main_uid=observed.main_uid,
            session_binding_id=observed.session_binding_id,
            claimed_registration_evidence_sha256=observed.claimed_registration_evidence_sha256,
            account_plan_sha256=observed.account_plan_sha256,
            account_packet_sha256=observed.account_packet_sha256,
            native_proof_sha256=observed.native_proof_sha256,
            native_readback_sha256=observed.native_readback_sha256,
            observed_at=observed.observed_at,
            expires_at=observed.expires_at,
        )
    except Exception:  # noqa: BLE001 -- do not expose native account details
        raise DemoPublicRouteBindingError("demo_public_route_binding_denied") from None
