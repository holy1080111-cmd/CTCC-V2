# Original-source financial operands V2

`consume_owned_native_account_financial_operands_v2` consumes the same owner
returned by `acquire_native_account_risk_assembly_v1`. The existing V1 consumer,
receipt, policy and arithmetic remain unchanged. The V2 consumer is an additional
diagnostic result, never an account revision, clear-stop permission, Arm request
or order capability.

The owner is removed synchronously before an awaitable is returned. History and
current coverage are each acquired and MOVEd once. Both original source anchors,
the original current-source expiry, monotonic deadline and Control lease remain
bound; the earliest deadline applies. The original B3 full-prefix/history and
B5/Control/current-custody replay surrounds calculation and runs again before
publication. Source files and fixed dependency functions are checked by the
assembly's existing inventory fence around every cross-source await. A final
replay cannot replace the original source bundle or mint later calculation
timestamps. Cancellation or late refusal preserves already committed source
evidence and never restores an owner.

## Bounded closed-run amount

The pure V2 reducer verifies the actual native positions archive format against
its original plan, four-field account identity, session, raw account/config
observations and page chain. It does not retag or convert a native archive into
the older diagnostic position archive format. It then reuses the existing V8
closed-run arithmetic with original B1/B3 fills, holding-position witnesses and
instrument metadata. Position totals can cover only one uniquely reconciled run.
The gross amount, fill fees, explicit position funding component, settlement
component and total must satisfy the original strict equations. Missing or
conflicting required components leave the run amount unknown.

An original linear instrument and matching position/witness `ccy=USDT` can prove
the currency of that bounded run. An absent global USD-contract settlement
setting remains an account-completeness gap; it does not erase this independent
bounded amount. The reducer retains the observed setting as `null` and does not
invent `settleCcy` in source bytes or a positive fixture.

## Funding bill observations

The existing AllProduct source verifier, funding classification and consistency
audit run on the original bill page chains. Every candidate retains its original
identity, row digest, recent/archive locators, signed `pnl`, source balance-update
timestamp, payment/balance-change agreement and fee-field evidence. Missing fee
or balance change remains unknown; conflicting identity variants are retained.
A candidate amount is exposed only when its original `pnl` and `balChg` agree,
the original fee is explicitly zero and no conflicting identity variant exists.
This conditional amount does not establish a complete payment population or
an account funding accrual event. Bill generation/update time is never relabelled
as accrual time. Older regional inputs lacking the required AllProduct audit
provenance retain an explicit audit gap.

## Unfinished obligations

All outputs retain `admission=DENY` and execution authority false. Daily/weekly
complete net PnL, funding accrual attribution, current/history tail finality,
loss-streak seed and historical native HWM remain unknown. The historical B1/B3
prefix is not upgraded to native ownership by this reducer. No amount is used to
clear an emergency stop, publish an account revision or allow a trade.

The focused unit cases declare original synthetic rows and native archive
bytes, replace only fixed acquisition/SQL/OS provider seams and exercise actual
parsing, arithmetic and owner fencing. They are not PostgreSQL, TLS or opaque
native-source acceptance. Source authoring alone provides no executed-test or
production acceptance evidence.
