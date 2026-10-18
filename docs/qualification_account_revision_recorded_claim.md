# Recorded account-revision source claim (DENY only)

DB0043 already binds one caller-written Demo ledger revision to immutable B1
history/current capture heads and their observation indexes. The new read-only
`read_denied_recorded_source_claim` takes only the exact scope and that witness
digest. It verifies the old witness, replays the original V7 current B1 chain
and measured body EOF, then derives account identity/mode, settlement equity,
exchange available margin and the unfiltered position, ordinary-pending and
eight algo-pending row counts. It compares the two balance amounts with the
stored caller claims using exact rational arithmetic. Missing source values
remain `null`; mismatches become explicit blocking reasons. The claim is
recomputed under the account UID lock in two independent database sessions,
and the original witness is read again before returning the canonical receipt.

This content-addressed receipt is a historical projection from recorded
sources, **not** a new durable account revision or a current portfolio. It
contains no source-authenticity, flat-start, reservation, recheck or order
authority. The original witness bytes and DB0043 schema are unchanged, so
older witnesses remain replayable. A direct pure helper call cannot prove
native acquisition; only the repository path binds an existing immutable
witness and two locked B1 readbacks. The original B1 capture can still be
synthetic, and the account registration site, credential session and outbound
IP are not authenticated by this projection. The existing
`_require_owned_account_revision` denial remains in place.

The next positive issuer needs an authenticated source session and registered
region, complete page-chain and economic history, native timing, local
reservation/intent/uncertain inventory, protection, a source-derived full
`PortfolioRiskSnapshot`, and a separately durable issuer-owned revision under
the same UID lock. A stored caller revision plus a matching recorded balance
is insufficient.

Unit tests exercise exact comparison, a divergent caller balance, unknown
settlement balance, bad source/witness pins and the authority flags. The
isolated PostgreSQL integration test exercises the separate-session readback
and deliberate second-read disagreement. It requires an explicit migrated
`DATABASE_URL`; a skipped test is not PostgreSQL acceptance.
