# One-shot G12 evidence publication

`app/trade_evidence/gates.py` connects the [G1–G11 engine](qualification_engine.md)
to the existing [evidence renderer and publisher](trade_evidence.md). It does not
register a runtime service, enable an account, or grant execution authority.

## Input and execution contract

`publish_qualification_evidence` takes the original raw market, intent, collected
quote, independent WS reference, explicit policies, portfolio inputs, consumed
event keys and evaluation time, together with the pre-evidence run to verify.
It always repeats the actual G1–G11 evaluation and compares the complete result;
a caller-signed digest, fabricated passing gates or an old receipt cannot replace
that work. A failed earlier gate returns unchanged, before reading the clock,
preparing charts or touching the filesystem.

For eleven passing gates, the function:

1. Samples a causal, aware UTC preparation time and rejects expired entry.
2. Rebuilds the stored G1 source and the original structural selection, comparing
   its complete audit with G8/G9, then prepares and validates the snapshot.
3. Renders four timeframe PNGs, a summary PNG and canonical `report.json`, checking
   that the report contains exactly the prepared snapshot. It hashes all six
   byte sequences before calling the fixed publisher.
4. Rechecks the clock and original expiry after rendering, before the publisher
   opens directories. The publisher writes without clobbering, fsyncs and reads
   back every file before observing its completion clock.
5. Validates the returned receipt, all six file identities and sizes, report and
   directory identity, and both publication clock observations. Only a newly
   written packet completed strictly before the original deadline passes G12.

The entry, SL, TP, trigger, zone, first eleven gate records and economics never
change during this process. The exclusive deadline is the earliest original
intent, trigger, zone or timing expiry. A rendering delay cannot renew it.

## Failure and replay semantics

Clock reversal, malformed clock values, preparation/render errors, publisher
errors and inconsistent receipts fail closed. An identical already-present
packet remains on disk but returns `evidence_already_published`, not a new G12
permission. There is deliberately no API that accepts an old receipt as a permit.

Expiry detected after successful publication preserves the actual receipt and
files while failing G12. Failed publication may have left partial files; the
coordinator neither overwrites nor silently repairs them. Without a validated
receipt, the published-file count is unknown, not zero. Error details are bounded.

The stored `report.json` contains the original eleven-gate snapshot. Including
its own completed G12 receipt would create a circular hash. Its presentation
flags therefore remain `evidence_gate=not_evaluated`,
`execution_recheck=not_evaluated` and `gate_assessments_verified=false`.
The separate `EvidenceGateRun` records this invocation's actual replay and G12
result; a serialized instance or its fingerprint alone is not external proof.

All twelve passing gates still yield `qualified=false`. Source authentication,
account authentication, atomic reservation, execution recheck and execution
authority remain explicitly false. The injected clock is a trusted runtime
observation for this local call, not authenticated exchange time.

## Verification and platform boundary

Tests distinguish synthetic publisher-contract responses from real native
filesystem publication. Native POSIX cases must actually render, write and hash
six files, reject identical reuse, reject pre-write expiry and preserve the real
receipt on post-write expiry. Windows-specific tests check drive-root rejection
and fail-closed behavior when a native directory pin is unavailable; these do
not establish successful Windows full-chain publication.

A further Windows test invokes the real renderer/publisher on an owned temporary
root, without mocking the native pin. It requires either actual six-file readback
or the specific permission-denied failure with no receipt and an untouched owned
root; unrelated exceptions are failures, not skips. The earlier restricted child
process still denied ancestor access. The main process subsequently reran the
exact frozen `7c7e9b5` source under the user's updated permissions: both long and
short cases actually wrote and read back all six files. This is scoped native
Windows success, not a claim that the earlier access denial never happened or
that filesystem checks were weakened. See the [checkpoint acceptance](evidence/qualification_pipeline_20260912.md).
Bounded error details preserve up to eight causal exception layers for diagnosis.

Long and short examples are synthetic OHLC and fictional typed Demo-account
claims, not observed exchange samples. Existing source/renderer/storage tests
remain applicable, but each new source checkpoint requires its own manifest,
frozen Linux full regression and matching CI result. Results are recorded in the
local `reports/entry-acceptance-20260912` checkpoint receipts, not inferred from
an earlier commit's acceptance.

## Remaining implementation

A full post-publication recheck still must request new market/account data,
retain the original event and bracket, validate intervening confirmed candles,
structure/volatility/location and worst executable economics, and reserve risk
and consume the event atomically. Trusted adapters, durable pre-submit intent,
all Demo submit routes, outbox, realized forensics and genuine shadow/soak
acceptance remain separate work in the [implementation plan](entry_qualification_implementation.md).
