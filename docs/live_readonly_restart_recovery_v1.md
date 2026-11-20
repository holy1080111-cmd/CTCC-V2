# Explicit Live read-only restart recovery

The existing local Live page's reconcile action calls
`POST /api/live-tool/reconcile`, then the fixed
`owned_lifecycle_v1.reconcile_pending_owned_live_intent_v1` delegate. Only the
original reservation digest is accepted. No entry request, Arm, submit retry,
leverage change, protection cancellation or close permission is accepted here.

Recovery opens the genuine private runtime and restricted reconciler, verifies
its actual catalog/key identity, commits a negative stop, and independently
reads the stopped Control and durable original intent before creating an HTTP
client. Missing or contradictory local state refuses recovery. A previously
resolved journal returns its historical result without claiming current
protection or opening a source session.

An unresolved intent gets a **new** GET-only credential lease in the current
task/process. Current settings must pin its exact Live UID/mainUid, region and
origin. The first signed account/config response verifies identity, Read+Trade
only, IP binding and current account modes before order reads. The original
private raw custody retains each request's bytes and measured causal stamps.
The existing full current-account collector and exact order/protection readers
then run; order-specific fills use `/api/v5/trade/fills-history`, with `billId`
pagination through an explicit empty terminal page. The original fixed entry,
SL, TP, client IDs, contracts, leverage and margin mode remain unchanged.

`native_live_intents_v1.created_at` is read as `intent_recorded_at`. It is a
historical lower bound, **not** an original HTTP request start. If a signed
prior lifecycle proof retained an original submission UTC, that UTC remains a
separate historical value. Old process monotonic clocks are never compared to
or relabelled as current clocks. The original order creation must still fit the
immutable intent deadline. Every new GET and validation uses the new measured
barrier and unchanged current freshness/causality checks.

A fresh exact exchange order can establish that the original client order
exists. This is retained as a distinct `recovery_submission_observation`, never
a fabricated original HTTP response/header/start. Missing orders, timeouts,
missing fill terminals and unknown exposure retain the reservation. Exact
canceled zero-fill FOK plus complete fresh fills/account evidence can resolve
the original hold through the existing signed SQL protocol. An exact protected
position is recognized as `PROTECTED_HOLD`; risk remains reserved, economics is
unknown, and qualification is not reestablished. A filled-flat original OCO
without verified exit lineage remains `UNCERTAIN`.

The existing source-owned HMAC boundary independently reopens raw custody,
replays current source and history operands, and binds the new proof to the
original journal/intent before SQL append and committed readback. The public
result retains only the existing nine safe fields. It never exposes private
routes, credential material, UID, historical raw pages or execution authority.

Validation: synthetic UTC/new-process-clock and malformed-source cases,
orchestration ordering/cancellation/foreign-task tests, real local HTTP routing
over explicitly synthetic SQL/source handoffs, and unchanged raw-reader,
private-custody/runtime and manual-page tests. A new actual-PostgreSQL test is
authored and collection-checked only; it measures the genuine restricted query
and two committed reads of `i.created_at` with a declared synthetic key-file
handoff. No venue, installed credentials, Arm, order or Micro Live acceptance
was performed by this slice.

Official parameter contract checked 2026-11-21:
[OKX transaction details documentation](https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-transaction-details-last-3-months).
