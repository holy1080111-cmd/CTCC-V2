"""Private native historical reporting source gate; legacy calls remain denied."""


class DeliverySourceAuthorityUnavailable(ValueError):
    """Fixed refusal; carries no account, token, DSN or remote response."""


def require_issuer_backed_projection(
    permit=None, *, adapter=None, payload=None, claim=None
):
    """A boolean, local pair or old projection cannot authorize any HTTP call."""
    if permit is None:
        raise DeliverySourceAuthorityUnavailable(
            "notion_outbox_submission_source_authority_unavailable"
        )
    from app.trade_evidence import native_notion_delivery_source_v55 as native

    try:
        native.consume_native_notion_dispatch_v55(
            permit,
            adapter=adapter,
            payload=payload,
            claim=claim,
        )
    except Exception:  # noqa: BLE001 -- Never expose delivery-source exceptions to the outbox.
        raise DeliverySourceAuthorityUnavailable(
            "notion_outbox_submission_source_authority_unavailable"
        ) from None
