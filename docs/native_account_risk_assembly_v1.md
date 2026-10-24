# Native account financial assembly and cashflow windows, version 1

This version joins original native history ownership to the current-account
ownership already held by controlled writer coverage. It is a bootstrap
financial-input path, not an armed trading permit. Missing funding attribution,
execution-time population finality, the current tail, a complete loss-streak
seed or a policy-matching high-water predecessor must remain unknown. A
successful component calculation must not publish a complete account revision
or enable an exchange write.

## Cashflow time and amounts

The financial window uses each original fill's `fillTime`, not its source `ts`
or the final close time of its position. The current
[OKX REST documentation](https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-transaction-details-last-3-months)
distinguishes execution time from record-generation time. A fee is signed:
negative is a charge, positive is a rebate. Fill PnL and the signed fee are
combined in the original settlement currency, with exact rational arithmetic.
For derivatives, reported quantities are contracts. These definitions alone
prove neither a complete execution-time population nor funding accrual.

For the original native validation instant `t`, the daily window includes
`UTC midnight <= event time <= t`; the rolling seven-day window includes
`t - 7 days < event time <= t`. Each loss is `max(0, -sum(net cashflows))`.
Gains and losses offset within the same window. Fees on opening trades and
partial closes belong to their own execution windows. A whole-position result
remains a forensic consistency check, not a replacement for window cashflows.
The retained observed fill subtotal ends at the original history cutoff, which
can precede `t`; it is labelled separately from the unknown complete net window.
A history cutoff before today's start cannot establish a zero daily subtotal.

For example, yesterday's opening fee of -1 and partial close of +100 with fee
-1, followed by today's close of -50 with fee -1, give a whole-trade net +47
but today's observed fill cashflow -51. Missing funding still prevents calling
that observed amount the complete daily net loss.

Identical recent/history representations retain their original evidence
locators and contribute once. Conflicting representations deny the result.
Bill mirrors of a fill's fee or PnL are cross-checks, not additional cashflows.
Raw page order and original bytes remain intact. Unsupported currency,
unpaired movements and ambiguous position-ID reuse cannot disappear by
filtering or conversion to zero.

Loss streak is a different population: complete negative lifecycle outcomes
increment it, complete positive outcomes reset it, and a zero outcome preserves
the existing streak. A partial fill, an unmatched funding movement or a missing
seed cannot become a complete lifecycle outcome.

## Private ownership handoff

The native route acquires history first, then controlled coverage. Coverage
acquires and moves the original V4 current-account owner once; assembly must
not acquire or move a second copy of that owner. Both original clock anchors
and deadlines remain in force, with the shorter remaining lifetime controlling
the join. Acquisition does not refresh either expiry.

Private keyed moves burn the public input owners synchronously. Consumption
likewise burns the composite owner before returning its fixed awaitable.
Never-awaited calls, cancellation, a foreign task and exceptions do not restore
capabilities. Memory release never deletes committed source evidence, native
companions, state journals or safety latches.

History replay covers every accepted sequence from 1 through its pinned head,
with original B1 bytes and B3 membership. Available native companions and the
original source bundle's DB0046 seal receive separate readback; this does not
prove native companions for every historical member. A trustworthy newest batch
does not certify an older unproved prefix, which remains explicitly unknown.
Current coverage replay retains its original V4
source, B5, Control revision, lease and durable active-state checks. Assembly
also pins the union of its actual source files and original private functions.
Saved receipts, DTOs, caller clocks, flags and reported hashes do not issue
current ownership.

## Bootstrap and operational continuation

DB0048 bootstrap requires the original disarmed, emergency-stopped Control
state. Arming or clearing that stop changes the Control state and invalidates
the old binding. The operational route therefore still needs a separately
reviewed continuation contract: verify the durable DB0048 journal lineage,
accept only authorized Control transitions, and acquire a genuinely new V4
source before issuing a new one-use owner. It must not silently rebind old
owners or relatch the emergency stop for every trading decision.

Post-G12 execution needs new source requests that start strictly after the
publication barrier. Neither this bootstrap nor stored DB0048 evidence can
substitute for that acquisition.

## Acceptance boundary

Unit fixtures can verify arithmetic, denial behavior and one-use handoff.
Isolated PostgreSQL tests can verify durable readback and concurrency. They do
not establish authenticated OKX completeness, historical point-in-time
availability, Demo execution or Micro Live acceptance. Source authoring and
prepared tests are not executed acceptance. The evidence harness records
actual results separately; engineering-complete, Micro-Live-ready and final
completion flags remain zero until their full acceptance matrices pass.
