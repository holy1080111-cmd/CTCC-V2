"""Source-local phase hook; the database service owns the actual witness.

This keeps passive public acquisition independent of account/database modules.
The hook cannot alter a source packet and grants no authority by itself.
"""

from app.public_market_source.public_market_receipts import PublicReceiptError
from app.public_market_source.public_receipt_storage import (
    ControlledPublicReceiptJournal,
)

_ISSUER = object()


class OwnedPublicCheckpointHook:
    __slots__ = ("_delegate", "_journal")

    def __init__(self, issuer, journal, delegate):
        if issuer is not _ISSUER or type(journal) is not ControlledPublicReceiptJournal:
            raise PublicReceiptError("controlled_witness_required")
        self._journal = journal
        self._delegate = delegate

    async def open(self, journal, plan):
        if journal is not self._journal:
            raise PublicReceiptError("controlled_witness_journal_changed")
        await self._delegate.open(journal, plan)

    async def after_attempt(self, journal, disposition):
        if journal is not self._journal:
            raise PublicReceiptError("controlled_witness_journal_changed")
        await self._delegate.after_attempt(journal, disposition)

    async def after_capture(self, journal):
        if journal is not self._journal:
            raise PublicReceiptError("controlled_witness_journal_changed")
        await self._delegate.after_capture(journal)

    @property
    def before_capture_sequence(self):
        return self._delegate.before_capture_sequence
