# Read-only recovery after a controlled Live close

The existing Live page's reconcile action also covers a restart after a
controlled-close claim. It never reuses old consent, restores Arm, submits
another close, cancels protection, or changes leverage. The negative database
stop and independent stopped-Control readback happen before any venue GET.

The restricted reconciler performs two independent committed reads of the
original immutable close intent and both optional action claims. Their original
domain-specific HMACs, canonical request bytes, database timestamps and exact
original reservation/intent are replayed. A private per-task recovery scope
pins those records independently of the returned DATA objects. A prepared
intent without a close claim cannot authorize a POST after the stop: its old
Control revision has been revoked. A persisted close claim selects only the
controlled-close recovery source. Failure never falls back to original OCO
classification.

A new read-only lease re-verifies current settings, exact UID/mainUid, region,
credential session, permissions, IP binding and modes. Retained original entry
and protection IDs must come from the authenticated protected lifecycle proof;
all retained bindings must agree. The completed orders-history-archive partition
runs from the database close-claim timestamp to a new fixed cutoff, with the
official order-ID cursor, explicit empty terminal page and original raw custody.
Every matching close client ID through the cutoff is counted. There must be
exactly one request, created inside the immutable original close-claim window.
Missing history, duplicate identities, later client-ID reuse, a missing order or
a timeout cannot establish completion.

Order creation time establishes request identity only. It is never fill time,
funding time or realized PnL. Fresh GETs then read that exact close order ID,
the original entry order, complete bill-ID fills-history chains for both, and
stable second order/protection reads. They must confirm full opposite-side FOK,
reduce-only, isolated/net, exact contracts/leverage and the original structural
price bound. Actual execution can finish after the old request expiry; its
unchanged `fillTime` must fit the authenticated fill/order/receipt chronology.
The exact original fixed mark OCO must be canceled without a triggered child,
and full current-account evidence must show zero positions, ordinary pending
orders and pending algos. An active protection or any unknown state retains
the original risk hold. Recovery performs no cleanup write.

There is one explicit nonexecution branch: the exact unique retained close
must be a stable terminal canceled FOK with zero accumulated fill, empty
average price and complete empty fills-history. Its source is verified at its
own measured receipt before any further collection. Only that positive proof
permits a **new** original OCO entry/algo/child/fill/global-flat capture. Both
groups and the original close claim are replayed and hashed into the signed
`original_oco` terminal result. Partial/ambiguous/missing close evidence never
selects this branch. An existing cancel claim contradicts zero effect and is
refused. This creates no new close, cancel, Arm or entry permission.

Before signing, the owner independently reopens the complete archive inventory,
plan, raw pages, both identity observations and custody receipt. Every archive
and terminal response is independently checked against the private retained raw
GET bank and current lease. The unchanged five-second freshness policy still
applies to mutable terminal data. The signed terminal proof uses
`exit_kind=controlled_micro_close` and binds the original close intent, close
claim, consent and any original cancel claim. SQL and an independent committed
readback control risk resolution. Economics remain unknown, and no old POST
response/start or new execution authority is invented.

Validation in this slice uses explicitly synthetic Live raw DATA and published
test-only HMAC material. Nine actual TLS/PostgreSQL cases are authored and
collection-checked, including original HMAC readback, complete flat resolution,
ambiguous client-ID history, partial fill, active protection and original OCO
after positively proven zero effect. They are not a
claim of local database execution, provider acceptance or Micro Live acceptance.

The separate native-clock timing test runs the ordered lower GET readers,
complete private archive/current custody and compiled-source guard with a
declared 5 ms MockTransport and one allowed instrument. Its DTOs keep
`synthetic_mock=true`; it neither exercises the private source owner/HMAC signer
nor claims exchange latency or venue acceptance. No source clock is replaced
and both mutable terminal freshness and post-capture guard timing retain the
existing five-second bounds.
