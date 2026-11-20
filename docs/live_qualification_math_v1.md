# Live qualification calculation

`app.okx_live.qualification_math_v1` recomputes the existing qualification
arithmetic from a `LivePublicEvaluationV1`. Its bounded in-memory reader replays
the entire original public raw journal, component packet, anonymous Global
origin/environment/report, inventory and Live envelope. It then reprojects the
Live quote, WS reference and market and compares them with the supplied DATA.
No Demo quote or `www.okx.com` transport record is constructed.

`evaluate_live_qualification_math_v1` requires an explicit original intent,
policy and consumed-event set. `None` for that set means unknown and waits at
G6; it never becomes an empty account ledger. G1 rebuilds candle quality and
indicators. G2–G4 reuse the four base strategies or the four existing versioned
history-family evaluators. Missing family chronology/permission stays blocked.
G5–G9 use the original trigger, source zone and joint structural selector.
G10 costs both the unchanged candidate and executable ask/bid reference.

`recheck_live_qualification_math_v1` first replays the original computation. An
original G1–G10 failure cannot be repaired by a better current quote. It requires
the same invocation and the separately supplied actual publication candidate
digest, a post-publication source and strictly later request starts. The G12
snapshot digest, stable geometry digest and math receipt digest have distinct
meanings. Recheck uses the original event path, history, zone, policy, entry,
SL/TP and original selection audit; it never extracts a replacement current
trigger or selects a new current bracket. Expiry can only shorten.

The shared fixed-protection implementation has closed Demo/Live profiles. The
existing Demo API retains its original exact quote contract and receipts. The
Live wrapper accepts only `LiveExecutableQuoteV1` and its own inspector, using
the same unchanged fixed-bracket math and source checks.

Missing typed Live financial/account inputs ends at G11 with
`live_financial_snapshot_required`. The result exposes exact projected risk
operands for the separate genuine financial adapter. It does not manufacture a
loss-history seed, complete account, availability proof or authority. Existing
typed Live portfolio claim calculations remain pure DATA; both candidate and
executable samples must pass. The coordinator must supply/replay the genuine
financial, local-ledger and control sources before proceeding.

Every output has `execution_authority=False`, `account_complete=False`,
`source_authenticity_verified=False` and `atomic_risk_reserved=False`.
Constructing or replaying a result does not collect an account, arm Live,
publish G12, reserve risk, create an intent or submit an order. Synthetic unit
fixtures and arithmetic regression results are not venue acceptance evidence.
