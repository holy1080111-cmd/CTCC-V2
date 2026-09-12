# Recorded-only candidate recheck

This increment implements the offline calculations behind R1–R4. It does **not**
complete the trusted one-shot runtime, authenticate an account or market feed,
reserve risk, submit an order, or add the final qualification gate. See the
[remaining R1–R7 plan](qualification_recheck_plan.md) and
[full implementation tracker](entry_qualification_implementation.md).

## Fixed original facts and current inputs

| Module | What is actually checked | What its result cannot prove |
| --- | --- | --- |
| `recheck_models.py` | Strict recorded G1–G12 origin; full original G1–G11/source/snapshot replay; original event, policy, geometry, six-file pins and earliest deadline | That this invocation actually published the packet, or may resume from an old receipt |
| `current_conditions.py` | Actual current G1 reconstruction, then original strategy's G2 route, G3 HTF permission and G4 required/veto/score/fresh-cost checks | A new trigger, original-event survival, or new entry permission |
| `continuation.py` | Original source/analysis/event replay; every original confirmed OHLC field retained; append-only, continuous, native-grid four-TF history through required closed tails; known post-setup invalidation | Unobserved intrabar path or authenticated per-TF collection |
| `fixed_protection.py` | Replay original selector/audit once; retain original entry/SL/TP and anchor; rebuild current ATR/anchors, fresh quote, noise/liquidity clearance and nearer opposing target barriers | Event continuation, current strategy conditions, costs, account safety or re-selection of a better bracket |
| `current_risk.py` | Actual candidate/executable economics and two fresh same-account portfolio scenarios with original size/leverage/policies | Authentic account completeness, exact reservation amounts, any reservation or a guaranteed fill |
| `recheck.py` | Ordered offline composition and source replay, recorded quote barrier, current conditions, continuation, original timing/zone, fixed protection and current risk | R5 adapters, R6 atomic ledger, R7 current-invocation publication or full Execution Recheck |

Every origin/assessment remains explicitly recorded-only. Loading a saved
`EvidenceGateRun` can support diagnostics, never runtime admission. A future
runtime must itself call G12 and own the collection/locking dependencies; it
cannot obtain authority by deserializing or hashing these records.

The fixed deadline is the minimum of the original intent, trigger, zone and
timing deadlines. Equality is expiration. There is no reprice, resized order,
new leverage, replacement report/event, renewed expiry, or changed policy to
repair a rejected candidate. Original source replay uses the original inputs,
not the later market snapshot.

## Closed history is not a complete price path

The continuation check requires the complete original history and append-only
extensions; it rejects rewriting any OHLC/volume/confirmation field, truncation,
missing required closes, duplicates, gaps, out-of-order/off-grid/future bars, and
the original event's expiry. It does not silently sort, fill holes or roll the
history window. A later source needing more than the bounded 1024-bar contract
is rejected instead of discarding original evidence.

For all 4H/1H/15m/5m frames it records the original/latest confirmed close,
expected closed tail, appended count and remaining blind interval. A confirmed
bar wholly after setup touching the original invalidation cancels the candidate,
even if price later recovered. A bar straddling setup does not establish
intrabar ordering. A fresh point quote cannot fill the remaining blind interval.
`complete_path_verified` remains false even when the sampled checks pass.

The merged market receipt is not a per-TF raw-capture receipt. The offline
coordinator can check supplied causal/barrier claims but cannot prove each raw
request was actually made; that is an explicit R5 adapter requirement.

## Fixed protection and account calculations

The new market is never passed to the full bracket selector to adopt its newly
preferred stop/target. Only the original selection is replayed. Current ATR and
the existing bounded anchor universe supply additional constraints on the
original bracket. Noise and liquidity thresholds compare exact rational values;
Decimal displays are only reporting, with the rational operands retained.

A real synthetic-source cancellation case is retained: the next 15-minute
confirmed bar can confirm a closer opposing swing. Continuation and the original
entry zone still pass, but the old TP now lies beyond a newly confirmed barrier.
The candidate is cancelled, not assigned a closer TP.

Current risk uses one unchanged account identity, contract definition, order size,
leverage and original policies. Instrument source stamps may refresh; a changed
contract specification or classification cancels the old candidate. Missing
current account/guard/instrument data does not fall back to the old G11 evidence.

Both original-entry and sampled ask (long) / bid (short) economics must pass
before any portfolio evaluation. Candidate portfolio failure stops the second
scenario. Otherwise both scenario portfolios must pass. Tests actually exercise
the case where candidate risk passes but executable-reference risk exceeds the
same current portfolio cap; a displayed maximum alone is not sufficient.

Maximum risk/notional/margin displays describe the worse of these **two sampled
scenarios**, not every possible market fill. They are not exact atomic-ledger
reservation amounts. R6 must use validated original operands with exact arithmetic
or an explicit conservative rounding contract, and reserve one proposal, not
double-count the two scenarios as two orders.

## Boundary and acceptance

The recorded coordinator stops at the first failed check and preserves its
subresults. Even `computational_checks_passed=True` leaves runtime admission,
publication-observed-here, source/account authenticity, atomic reservation,
complete-path verification and execution-recheck authority false. No thirteenth
PASS is appended; the original twelve-gate candidate stays unqualified.

New tests use explicitly synthetic OHLC/account data. MockTransport quote tests
exercise the real three-endpoint parsing/provenance collector, while a synthetic
publisher-contract test is never described as actual filesystem publication.
Separate native filesystem cases publish/read back all six files before three
MockTransport requests and then run actual recorded checks in both directions.
Their synthetic clocks and account data remain explicit; a restricted Windows
permission-denial result is logged separately and never called a successful
publication. Platform receipts must inspect which native outcome occurred.
Working-source tests and frozen-source full/platform/CI acceptance are distinct;
the exact committed source receipt in `reports/entry-acceptance-20260912` records
the latter when it has actually finished. The public
[checkpoint record](evidence/qualification_recorded_recheck_20260912.md) separates
code-source acceptance from any subsequent documentation-only commit.
Reports and real public diagnostic
packets are excluded from public Git, the manifest and the Docker context.

No existing deployment, trading flag, OS clock or ACL is changed by this
increment. New-pipeline genuine Shadow/Demo samples remain zero. Full runtime,
historical regime admission, controlled Demo submission, outbox, forensics,
real soak samples and final evidence examples remain separate unfinished work.
