# Captured account instrument metadata

`account_metadata.derive_captured_instrument_metadata` closes the gap between the
captured `/account/instruments` rows and `InstrumentEvidence`. It replays the
externally pinned account packet, derives required identities from the plan and
all returned exposure/history rows (including aggregate `posData` and bills), and
uses the exact captured JSON row. Optional caller rows are assertions only: each
supplied field must match the source exactly. A missing source row or conflicting
contract value, currency, type, lot, minimum, tick, leverage or state is never
repaired with caller data.

The controlled session now invokes this producer after its before/after DB
checkpoint checks. Its separately versioned runtime receipt is
`ctcc.controlled_demo_account_runtime.v2`, binding the metadata receipt hash.
Historical account packet v2/v3/v4 plans, bytes and hash calculations are unchanged.

Each derived row binds its SHA256, original response body hash, page receipt hash,
packet hash and plan hash. The complete captured row retains `instType`, `ctType`,
`ctVal`, `ctValCcy`, `ctMult`, `baseCcy`, `quoteCcy`, `settleCcy`, `lotSz`, `minSz`
and `tickSz` exactly as received, including absence. Contracts are not converted
to base/quote currency quantities. The existing materializer still rejects risk
mapping for unsupported/inverse contracts. Missing or nonpositive tick size is
an explicit runtime blocker.

This endpoint has no row as-of timestamp. The producer records measured body
completion as local row availability and labels that semantic in its receipt;
it does not invent an exchange timestamp. Required metadata absent from this
SWAP-scoped catalog remains missing. Non-SWAP products remain unsupported, and a
bill with no documented product type stays unknown. SPOT/MARGIN can share an
instrument ID; that does not convert either product into a SWAP contract.
More than 128 required instrument identities rejects the attempt without
truncation. Empty inventories do not establish account inception or history.

## Remaining admission boundaries

| Boundary | What exists | Required engineering and evidence |
| --- | --- | --- |
| Source authenticity | Owned signing and verified TLS, exact configured host, no proxy/redirect/retry, one-use session; DTO replay is integrity only. | A source-specific registration verifier and controlled credential setup must bind the account's actual registration region. Durable replay of a saved packet/hash cannot restore process-local transport provenance. |
| Query completeness | v4 requires all 38 standard-product query chains, exact cursor identities and explicit empty terminals; current ordinary/algo inventory is unfiltered. | Add actual metadata catalogs for other products, including source-derived option families and event series; verify advanced-product liabilities/inventory. Query-chain completeness does not prove account-wide scope. |
| Metadata mapping | This producer binds the captured SWAP rows directly to mapping and lists every observed missing/unsupported instrument. | Add supported product risk/currency semantics and independently sourced cost/correlation evidence. These inputs and unsupported products cannot be promoted by an empty gap list here. |
| Retention and required seed | B1 retains raw history; B2a replays the exact journal/packet/page joins and derives bounded Global generation-time query coverage. | Lifecycle/loss-window coverage remains separate. Verify an opening seed or a source-derived strictly positive net reset with its full subsequent sequence; inception is one route, not universally required. A reset inside the loss window cannot fabricate its earlier seed. API terminals cannot prove history outside retention absent; late/conflicting discoveries remain evidence. |
| Peak and funding | Sampled balances and funding bills are available as raw evidence. | The old continuous-peak claim stays unverified; a new explicit persistent measured-HWM/DD-window policy need not claim an unsampled maximum. Held-position/accrual-event linkage remains required; bill-generation time cannot replace accrual time. |
| UID/session/revision | Exact UID and mainUid relationship are pinned separately; config before/after, environment, credential session, publication barrier and unchanged persisted DB claims are checked. | A trusted account-revision producer must atomically bind the verified account facts into the existing ledger, including non-flat local/exchange exposure union. The current checkpoint reader only verifies an existing revision; it does not authenticate old caller claims. |

The last four rows contain engineering work, not just absent credentials. Missing
historical source artifacts are external evidence requirements after their
producer/verifier exists. The bootstrap manifest reader currently proves only
bytes, scope and declared time ranges; all eight claimed artifacts together still
cannot authorize an account. No arbitrary 90-day substitute for documented
calendar retention, invented account start, zero loss seed, funding time or peak
is used.

Runtime admission remains `DENY`, packet/account authority flags remain false,
and no submission permit is issued. Positive synthetic tests show the source
mapping works without caller instrument DTOs; they do not authenticate OKX,
accept Demo trading, or complete the trusted-account acceptance gate.

```powershell
python -m scripts.hermetic_pytest -q tests/unit/test_qualification_account_metadata.py tests/unit/test_qualification_account_runtime.py
```
