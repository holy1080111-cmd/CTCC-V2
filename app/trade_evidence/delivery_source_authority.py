"""DENY-only final Notion source gate until issuer-backed DB readback exists."""


class DeliverySourceAuthorityUnavailable(ValueError):
    """Stable local refusal; carries no account, token, or remote response."""


def require_issuer_backed_projection() -> None:
    """A local outbox pair or old projection receipt is not delivery authority.

    The required immutable outcome, owned transport issuer, and projection
    readback cannot yet be joined. No caller-provided flag or callback can
    authorize a Notion request through this function.
    """
    raise DeliverySourceAuthorityUnavailable(
        "notion_outbox_submission_source_authority_unavailable"
    )
