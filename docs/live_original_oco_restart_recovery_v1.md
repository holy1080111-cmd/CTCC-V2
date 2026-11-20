# Original Live OCO terminal restart recovery

The existing explicit Live reconcile action can recognize a completed original
TP/SL without creating a local close order. It runs inside the stopped runtime
and fresh GET-only recovery lease described in
[read-only restart recovery](live_readonly_restart_recovery_v1.md).

Original identity comes from the retained, authenticated `PROTECTED_HOLD`
journal proof: exact primary order ID and actual protection algo ID. All
retained binding proofs must agree. Client IDs alone do not establish historical
uniqueness on OKX. Missing original identity, conflicting history or replacement
protection remains `UNCERTAIN`; the newest matching client ID is never adopted.
New protected proofs retain their actual source-observed algo ID.

The collector queries the retained algo ID and chooses one source path. An
effective original OCO requires new reads of the original entry order and its
complete fills-history chain, original algo, exact generated child order and
complete child fills-history, then stable entry/child/algo readbacks. A fresh
complete global current-account capture follows. The original entry/SL/TP,
client IDs, contracts, direction, isolated margin, net position mode and
leverage are fixed. Only one full market child from an unchanged mark-price OCO
is supported. Splits, partials, sub-algos, missing terminal pages, cursor reuse,
duplicate/conflicting fill IDs, foreign activity and unknown fields refuse a
terminal classification.

Every page is an explicit Live DTO from the existing zero-retry owned transport;
there is no Demo conversion or environment relabelling. New request, response,
validation, source age and collection-order checks remain active. Historical
order/fill/trigger UTC values remain separate from the new process clock. Entry
creation still fits the immutable original intent deadline. Source `fillTime`
is not replaced with bill/order generation time.

Terminal replay requires no nonzero global positions, ordinary pending orders
or any algo-pending family. Entry and exit full sizes, signed USDT fees, average
prices, original algo-to-child relation, trigger side/time and stable before/
after identities are recomputed from raw bytes. The source-owned signer
independently reopens custody, verifies all additional pages with the same fresh
lease, and recomputes the entire terminal result before signing. The unchanged
actual-0058 SQL protocol accepts `RECONCILED_FLAT` with `exit_kind=original_oco`
and independently reads back the committed resolution. The emergency stop and
disarmed state remain in place. There is no own-close intent, close consent,
submit retry or new execution authority in this path.

An absent original response remains explicitly absent. The separate recovery
submission observation records only a freshly observed exact exchange order;
`submission_started` is not reconstructed. Funding, realized economics, MFE,
MAE and profitability are not inferred by a flat-risk resolution.

Validation includes declared synthetic Live raw positives for long/short and
TP/SL, malformed/replacement/stale/partial/incomplete negatives, original-ID
source dispatch and terminal proof projection, plus the unchanged recovery,
reader, custody, runtime and manual-page regression. Four actual SQL/HMAC cases
are authored and collection-checked only: a complete source drives resolution;
changed protection, partial exit and missing fill terminal retain the original
hold. Their SQL roles/catalog, authenticated stored binding and constraints
are real; their published HMAC material and raw DTOs are explicitly synthetic.
No local database, venue, Arm, order or Micro Live acceptance was run here.

Official semantics checked 2026-11-21:
[OKX algo-order details](https://www.okx.com/docs-v5/en/#order-book-trading-algo-trading-get-algo-order-details)
and [order details](https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-order-details).
