# Native current account and original funding-bill source join

`capture_native_current_history_funding_join` is a separate, DENY-only V7
diagnostic. It preserves the existing V3 current/history receipt and the V2
funding-bill consistency receipt. Its new V4 receipt binds the same invocation's
V7 native current proof/readback to an original V5 B1 history chain, observed
both before the first current HTTP request and again under the exact-UID
PostgreSQL lock after current capture. The locked readback has separate V5
identity. Neither receipt publishes a `PortfolioRiskSnapshot`, account revision,
flat-start permission or execution authority.

The history source is the original signed Demo `GET /api/v5/account/bills` and
`GET /api/v5/account/bills-archive` B1 page chains. Each stream retains its own
`begin`/`end` filter on original bill `ts` (balance-update or generation time),
`limit=100` by the default V5 plan, and subsequent `after` requesting bills
older than the previous page's oldest `billId`. The original
collector and replay retain raw bytes, exact page and row hashes, predecessor
receipt, strict descending/exclusive cursor, identity, causal clocks and an
explicit empty terminal even after a short page. The same `billId` in recent
and archive streams must have identical original canonical row bytes. A wrong
scope, missing page, changed source, conflicting row or expired native lease
denies the join; no page is repaired or silently omitted.

The pre-HTTP read is a completed separate exact-UID transaction. It replays the
V5 source and computes the existing funding-bill consistency audit directly
from its original rows. The post-current locked read independently replays the
same audit from the original V5 chain while checking the V7 current chain and
local checkpoint. The native coordinator requires equal funding audit hashes,
equal source references and DB chain identity, plus the existing original
30-second V7 current lease and one-use current carrier. It retains already
committed B1 journals on late failure or cancellation. Receipts contain only
policy/source hashes and fixed fields, never raw UID, IP, rows or credentials.

The audit labels type-8 subtype-173/174 account bills as **payment candidates**
only when signed `pnl`, instrument, product and settlement currency agree. Its
field-consistency test compares original `pnl`, `balChg` and `fee`. Account-bill
`ts` is balance-update or generation time, **not** funding settlement/accrual
time. Empty bill chains do not prove zero funding. The new receipt therefore
retains `funding_accrual_at=null`, `net_funding_cashflow=null`,
`funding_history_complete=false`, `history_tail_closed=false`,
`account_complete=false`, `snapshot=null`, `execution_authority=false` and
`admission=DENY`. The V5 history has no retrospective native-clock proof;
registration region, provider credential session, current egress-IP match,
funding-event/held-inventory attribution, late-history finality, loss windows
and streak seed remain unresolved. No API request, order route, risk decision or
claim issuer is added by this diagnostic.

Focused synthetic unit tests exercise the pre/post original replay, policy and
source hashes, changed audit, expiry, pre-read failure and immutable denial.
The isolated PostgreSQL test for the original V7 locked join remains separate;
synthetic tests are not authenticated Demo or PostgreSQL acceptance.
