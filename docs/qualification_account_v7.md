# Current Demo account capture V7 (read-only)

The V7 plan and packet are separate from the sealed V2–V6 contracts. V7 adds
all eight `GET /api/v5/trade/orders-algo-pending` `ordType` queries listed in
the [OKX regional API guide](https://my.okx.com/docs-v5/en/#trading-account-rest-api-get-algo-order-list),
reviewed 2026-10-08: `conditional`, `oco`, `chase`, `trigger`,
`move_order_stop`, `iceberg`, `twap`, and `smart_iceberg`. Each query is separate
and unfiltered by instrument. The collector uses `after` with the last `algoId`
from the prior page and `limit=100`; a short nonempty page requires another
request and an empty terminal page. Duplicate IDs, cursor rollback, a missing
query, a conflicting `ordType`, or a page limit fail the entire capture. It
never substitutes `ordId`, `billId`, or a first-page count for pagination proof.

The [www.okx.com API guide](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-algo-order-list)
currently lists only four `ordType` values for this endpoint, whereas the
regional guide above lists eight. The user's `www.okx.com` login URL alone
does not establish which API contract applies to the exact Demo account.
First-party account-region evidence and read-only endpoint behavior must be
verified before treating V7 as accepted for that account. An unsupported type
or inconsistent response makes the current inventory incomplete; the collector
must not silently drop that query or fall back to four types.

The pending-algo response can contain `partially_effective` or
`partially_failed`, in addition to `live` and `pause`. V7 retains each exact
row and page hash for those states, but marks `algo_partial_state_unresolved`:
the parent state alone cannot prove residual exposure or active child
protection. Unknown states still fail parsing. `closeFraction="1"` is retained
as wire evidence of a full-close instruction, not proof of the currently
covered position size. These distinctions leave account completeness and all
execution authority false.

`CurrentDemoAccountCapturePlanV7` pins the Demo environment, exact UID and
main UID, controlled credential session, registration region and its matching
HTTPS origin, a registration-evidence hash, and the eight-query scope. The
evidence hash is an external claim until independently verified against a
first-party account/key record. The owned collector only issues signed Demo
GETs with `x-simulated-trading: 1`, no redirect, proxy inheritance, retry, or
order write. A private response carrying `Set-Cookie`, or any mutation of the
owned client's cookie jar during capture, aborts the batch before another signed
GET; cookies cannot become an implicit account-session or source-state input.
This transport check retains only its fixed rejection code, not cookie values.
The packet preserves raw response bytes, canonical JSON, receipt
and page hashes, causal clocks, query parameters, cursor chain, and account
identity before and after the inventory. Replay uses the exact V7 schema and
plan hash; V2–V6 packet hashes and replay contracts are unchanged.

The [OKX account-instruments response contract](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-instruments)
documents `instIdCode` as an Integer or `null`. A read-only Demo diagnostic on
2026-10-08 observed 167 SWAP rows, with `instIdCode` as an integer in every
row and roughly 179 kB of canonical JSON. V7 therefore permits a positive,
bounded integer only at `data[].instIdCode` in this endpoint. Every other
wire integer, floating-point token, misplaced code, or malformed code remains
rejected. V7's per-response default is the existing 256 KiB hard maximum;
oversized responses still fail closed. The diagnostic used a claimed global
route without independently verified registration provenance, so it does not
establish a trusted account source or authority.

V7 is a *current inventory lower layer*, not a complete account snapshot.
It does not cover historical cashflows, retention, local unresolved orders,
portfolio risk, or continuous peak/drawdown. Every packet remains
`account_complete=false`, `source_authenticity_verified=false`, and
`execution_authority=false`. No live account capture or Demo order is claimed
by the synthetic tests.

The separate [observed-inventory metadata diagnostic](account_v7_inventory_metadata_coverage.md)
compares instruments actually present in positions and pending orders with the
plan-declared metadata/leverage query. An undeclared exposure instrument stays
unknown; the diagnostic cannot borrow leverage from the candidate instrument
or make an empty inventory a positive metadata-coverage claim. It remains
`DENY` and does not change the V7 packet or current-source policies.

The separate V7 gateway policy (`ctcc.demo_account_gateway_causality.v2`)
replays the full original B1 journal and the V7 current-source policy. Its
receipt (`ctcc.demo_account_gateway_causality_audit.v2`) lists every page,
retains the exchange microsecond `inTime`/`outTime` pair when present, and
marks absent pairs as incomplete. It never substitutes host receipt time for
exchange processing time. V6 gateway policy/receipt V1 remain replayable.

An independent, DENY-only V7 native chronology proof now binds the historical
native-clock proof to those exact B1 pages. It requires all original gateway
pairs and all *recognized* source-row timestamps to be present and ordered
against their own page's `outTime`; rows with no recognized clock remain
explicitly counted as unknown. Its stored-companion entry point independently
reads back the old no-clobber proof before replay. The old V7 native proof
policy/receipt and issuance revocation remain unchanged. This new offline
receipt does not establish registration provenance, complete account/portfolio
state, current-use flat qualification, or execution authority; see
[the row/gateway contract](account_row_gateway_causality.md).

The V7 native current-components policy (`ctcc.native_demo_current_components.v2`)
maps all eight algo inventories to explicit counts and row/page hashes. It
preserves missing history, local exposure, protection, costs and portfolio risk
as `null` plus blockers. The versioned native proof, storage readback, clock
boundary and one-use raw-packet handoff now accept only an exact V7 flat source.
Their tests are synthetic; no authenticated complete account result has
passed. The projection still denies account completeness and execution
authority.

At exact source `b72c21025a0f9bcce8cfb0c7109a32b4d14af45f` on
2026-10-08, a separate, read-only diagnostic used the locally stored Demo
credential and captured/replayed all 17 V7 current streams in one invocation.
All 17 responses had process-local TLS peer evidence. The packet SHA256 was
`cc6ee41346be4f4b8079e50c9a84111c738ab77083e3f75c30c03da9df36906d`;
the redacted diagnostic is
`../validation-results/demo-v7-readonly-b72c210-20261008.json` (SHA256
`42e677f15e822009b40b30522cde676d9d1644c71d3b3b8e85f0892aeff4f780`).
This diagnostic did not retain private raw bytes after replay, prove the
account's registration region, bind a complete V5 history under one DB lock,
or establish account/portfolio completeness. It granted no order authority.

The V7 native runtime can also mint a one-use, same-task observation of the
signed account-config request's private origin after original proof and
readback. It binds the V7 diagnostic V3 policy, exact packet, UID, session,
TLS hostname and source receipt. This observation does not verify the
registration region or authenticity of later public-market bytes; the Demo
public route remains denied. Synthetic tests reject old diagnostic schema
and policy relabeling, consume the origin only once, and confirm that a
reviewed route still has no authority.

The pure native-clock proof has a separate flat V7 contract:
`ctcc.demo_account_native_clock_proof.v5` with policy
`ctcc.demo_account_native_clock_policy.v5`. It pins the V7 capture plan,
eight-algo query list, V7 current-source policy and receipt schema,
original B1 phase witnesses, host clock samples, public exchange-time probes,
TLS-labelled source readback, and a 30-second current-source bound. Pure replay
has a synthetic eight-empty-algo baseline and rejects a missing original algo
chain, an old V6 packet with V7 labels, wrong policy, and a four-algo V6
`flat` proof under its own revoked source policy. These synthetic TLS labels
test replay and private handoff contracts; they do not prove a real owned
transport or account acceptance. Historical V2/V3/V4 proof policy bytes
remain sealed.

The ordered V7 current/history diagnostic reads the entire previously
recorded V5 history chain in a separate exact-UID database transaction,
replays the original raw page chain and checks its source reference before
starting the new native HTTP session. After the current capture, it reads
both chains again under the UID lock and compares the original event and
database-timestamp digest. This establishes that a committed V5 chain was
visible to the diagnostic before its first HTTP request. A database insert
timestamp alone is not a commit timestamp and is not presented as one.
The diagnostic remains `DENY`: the historical retention tail, portfolio
components, protection and regional registration proof are still missing.

The offline materializer now applies its all-product boundary to V7 as well
as V5. A non-SWAP current position is recorded as unsupported even when its
reported quantity is zero; it cannot appear as a clean `zero_position` merely
because the older V5-only branch was skipped. A synthetic 17-stream V7 packet
exercises this case through packet replay and materialization. This correction
does not create a trusted account revision, complete history, or execution
permission.

The legacy private REST `pending_algo_orders` reader now queries the same
eight pending order types as V7, each through an explicit empty cursor
terminator and with every returned `ordType` checked against its query. Its
former four-type query could omit chase, iceberg, TWAP, and
smart iceberg exposure in a legacy reconciliation. As noted above, the global
guide still lists only four while a regional guide lists eight; a rejected
extra query fails reconciliation rather than producing a false flat result.
The prior 17-stream read-only Demo diagnostic observed the eight queries at
the configured global host, but did not prove regional registration. This
correction does not make the legacy reconciliation a complete account
snapshot: its order history remains a recent SWAP page, and V7's trusted
identity/history and portfolio conditions remain separate blockers.
