# Post-G12 current G1–G4 v2 diagnostic boundary

The V2 Demo issuer currently has no account-bound registration-region proof and
still carries Production public origins. It checks the trusted Demo-origin fence
before sampling the clock or publishing G12. New direct V2 invocations therefore
return `DENY` without creating G12 files or public/account requests. Previously
published evidence remains available for historical diagnostic replay, without
regaining eligibility.

Once a private account-bound Demo route issuer exists, `publish_capture_public_v2`
must publish and read back a new G12 report before it issues the one-use
post-publication public-source capability. After the native
collector returns, the coordinator checks that the raw v2 packet names the same
report and instrument, declares `post_publication`, and carries the exact G12
completion barrier. It then recomputes current G1 from that entire raw packet
under the original candidate's data policy and independently replays the G1
result against those same bytes and evaluation time. A changed or stale market
can therefore be recorded as a current G1 rejection without changing the
original candidate, entry, stop, target, event, policy, or expiry.

For `trend_pullback`, `breakout_continuation`, `fvg_return`, and
`order_block_return`, the coordinator also rebuilds the V2 market and strategy
context from the verified current G1 source, then evaluates current G2–G4
through the existing regime, condition, and strategy policies. A newly opposite
market rejects the original long or short direction. The current diagnostic
retains the original candidate entry and never selects a new event, zone, entry,
stop, target, or policy. The history-dependent strategies remain explicitly
unavailable until their source-derived chronology and protection policies are
complete.

When the four base gates pass, the coordinator replays the original detection
against the pinned original G1 source, then checks the new V2 source for exact
append-only confirmed candles in all four timeframes and any touch of the
original invalidation anchor. It reconstructs the *original* zone from the
original detection and policy, compares it with the G12 zone, and checks the
fixed candidate and fresh executable quote against that zone. A revised,
truncated, or rolled-off original candle fails this check. No event is extracted
from the new market as a replacement. The record states only closed-bar and
current-quote survival: OHLC and a quote cannot prove that no intrabar touch and
return occurred. `complete_path_verified` and full event-survival authority
remain false.

After that diagnostic passes, the coordinator uses the same raw V2 quote to
recalculate two projected cost scenarios: the unchanged original candidate
entry and the fresh executable ask (long) or bid (short). Both retain the G12
SL, TP and cost policy. The shared economics arithmetic charges explicit fee,
slippage, spread and adverse upcoming-settlement funding assumptions; a better
current reference cannot repair a candidate failure. The original policy's
quote-age limit also constrains the V2 funding exchange-return timestamp, so
an older rate is rejected even if the V2 transport profile accepts it. This
comparison records projected costs only. Actual account fee tier, realized
funding, margin cost and current structural protection remain unknown.

Before returning a positive public-only diagnostic code, the coordinator
replays G1 at its final measured time under the original data policy and
recomputes projected economics at that same time under the original cost
policy. A quote that ages out between the first calculation and this final
check is rejected. The `final_g1` field retains that later result. On a
final-time G1 failure, the returned diagnostic clears the earlier passing G1
and its dependent current-condition, event/zone and cost fields, so its sole
G1 result is the failed final replay. The earlier G1 and cost records describe
their own evaluation instants; they are not reusable freshness permits.

The returned `current_g1`, `final_g1`, `current_conditions`,
`original_event_zone`, and `projected_economics` are diagnostic records. Their own admission and the
coordinator's admission remain `DENY`, even when all public calculations pass. A
caller cannot provide G1, a PASS flag, a receipt, a barrier, or a replacement
market to this post-publication computation. Its raw public packet is still not
an account or execution permission.

`evaluate_current_base_conditions_v2` is a pure replay helper. Its supplied
G1 evaluation time can be replayed later with saved bytes, so a `passed`
result from that helper alone proves neither current freshness nor native
ownership. Only an enclosing, measured same-invocation owner can establish
those facts; no current diagnostic is an execution authority.

The full `publish_and_recheck` authority chain is still absent. The original
candidate in this entry comes from replayable caller inputs, rather than the
private initial native acquisition handoff. Source-derived history semantics for
the other strategies, the original event's complete intrabar path, fixed
original protection, account-specific executable economics, a complete
authenticated account/portfolio source, event and risk reservation, durable
intent and final dispatch remain unconnected. A future owner must prove all of
these in one invocation before it can ask the existing atomic ledger for a
reservation; the current diagnostic cannot be imported or promoted for that
purpose. No Demo or Live order follows from this slice.
