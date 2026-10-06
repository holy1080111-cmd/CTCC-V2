# Native original G1 diagnostic

`capture_owned_original_sources_v3` consumes a newly acquired V2 public packet
through the existing one-use, same-task native carrier. It does not accept a
caller market snapshot, candidate, G1 result, policy, PASS flag or publication
barrier. At the native observation time, it reconstructs the complete retained
public packet, evaluates G1 under `ctcc.native_original_g1_policy.v1`, and runs
the independent raw-byte G1 verifier. It records the fixed policy-record hash,
the actual data-policy hash, the consumed packet hash and the replayed G1 result
hash in `ctcc.original_owned_sources_diagnostic.v3`. A rejected G1 stops before
the private account read. A G1 arithmetic pass may proceed to the existing
one-use V6 current-account diagnostic, but still creates no candidate or G12.

The fixed policy is an **inspection policy**, not a calibrated trading or
admission policy. Its 200 confirmed bars are the evaluator's minimum supported
history; the five-second snapshot, quote and independent WS limits do not relax
the existing V2 quote profile. One closed-candle interval and 5/5/3/5 bps
reference-conflict/mark-dislocation/spread/absolute-funding limits are
conservative engineering rejection bounds. They have no measured calibration
for the intended instruments and market regimes. A changed value requires a new
policy version and source review. The existing synthetic numeric profile is not
imported or used. Neither a G1 pass nor the policy hash proves an economic edge.

The older V2 coordinator and exact V2 receipt remain unchanged. Both versions
return `admission=DENY`, `source_authenticity_verified=false`, and false
candidate, G12, account-completeness, recheck, reservation, execution and order
flags. The current Demo public-origin owner still refuses capture before network
IO until the regional Demo route mismatch is resolved and verified. Tests of the
V3 handoff use synthetic transport and do not establish real Demo account
provenance, G1–G12, or trading eligibility. The next source-derived candidate
steps require native instrument metadata, complete history, protection policy
and risk inputs; caller replay data cannot stand in for those sources.
