# V7 declared instrument metadata observation

The V7 current-source receipt can report `observed_flat=true` when the
positions, ordinary orders, and eight algo-order queries returned no rows.
That describes those inventory queries only. It does not prove that the
account instrument catalog or leverage settings contain the instruments
declared by the capture plan. A missing metadata row must remain unknown even
when all observed exposure queries are empty.

`verify_v7_declared_metadata` is an additive, read-only diagnostic. It replays
the exact original V7 B1 raw/page chain and V7 current-source policy from an
externally pinned source reference. For each plan-declared SWAP instrument it
records the account-instruments row hash, and the cross and isolated leverage
row hashes for the account's recorded position mode. Net mode requires a `net`
row; long/short mode requires both `long` and `short` rows per margin mode.
This follows the [OKX leverage-info response contract](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-leverage),
which states that both directions are returned in long/short mode. It also
checks that the instrument row reports an active linear SWAP settled in the
plan currency and has positive basic contract sizing fields. The
[account-instruments request](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-instruments)
is still scoped to `instType=SWAP`; this diagnostic cannot describe all account
products or establish executable contract economics.

Missing instrument rows, a missing or unexpected leverage side, empty or
nonpositive leverage, missing terms, and inactive or incompatible instruments
produce explicit `missing_or_unusable` findings. No value is inferred from
another instrument, margin mode, or side. The receipt contains row and page
hashes, not raw account identifiers, credentials, balances, or private headers.
A saved receipt is compared byte-for-byte with a fresh replay through
`verify_v7_declared_metadata_receipt`; constructing a receipt or recomputing a
hash does not establish exchange authenticity.

Even `declared_metadata_coverage_observed=true` means only that these
plan-declared metadata rows passed this bounded shape check at their recorded
source time. It does not verify the account's registration region, all-product
exposure, exact contract unit economics, ongoing market state, historical
cashflows, funding accrual, local reservations, protection, or account-wide
atomic revision. Every receipt remains `snapshot=null`,
`source_authenticity_verified=false`, `account_complete=false`,
`execution_authority=false`, and `DENY`. The existing V7 packet and current
source policies are unchanged; no prior receipt is reinterpreted.
