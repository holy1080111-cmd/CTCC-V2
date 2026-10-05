# Preregistered sweep history permission v1

This new pure evidence component retains the old sweep WAIT contract, router,
analysis, conditions, event extractor and score without modification. It grants
no order or source authority and is not registered in any qualification engine.
Future V6 integration remains separate work.

Policy ID: `ctcc-sweep-history-protection-v1`. The canonical `POLICY_BYTES` and
SHA256 pin in `sweep_history_permission.py` preregister the conditions below before
any behavioral test. No OOS result informs this policy.

Preregistered policy SHA256:
`abb0b422c2e5e0fa4c61596ebf9fb43aca57c2f13176f7ecc29770c4888f90ab`.
This was computed from static source literals without importing runtime code;
the runtime canonical digest must match during root validation.

P is the original 15m setup open (the previous 15m close), S its close, T the
original 5m trigger close, and C the current declared decision cutoff. Four valid
closed histories must reach each applicable cutoff with at least 200 rows.

At P, 4H/1H structure and bias must be measured neutral; all frames have measured
low/normal volatility, excluding both HTFs low. The 15m close must be inside its
confirmed support/resistance bracket with no BOS/CHoCH yet. Existing classifier
instability and mathematical-core insufficient/unstable states deny context.
Missing EMA/ATR cannot inherit the legacy neutral/normal defaults.

The original extractor still requires the prior opposed 15m trend, actual wick
beyond a previously confirmed swing and close reclaim, and co-confirmed break
through another prior confirmed swing. Same-bar intrabar ordering stays unknown.
The 5m false-to-true trigger bar must open at or after S. Setup age is at most
1800 seconds and trigger TTL is exactly the existing 300 seconds; replay never
extends it. Known post-setup invalidation remains a rejection.

At C, HTFs must still be measured neutral, controlled volatility retained, and
the unchanged router must return Unknown with exactly
`range_transition_history_missing`. Other Unknown causes remain denied. The
new historical proof addresses that one missing chronology; it never relabels
the old route as Trend. Every existing sweep required/veto condition and current
mathematical confirmation executes. Insufficient/opposed/unstable/blocked math
denies permission. `multi_timeframe_not_aligned` remains in the audit and is not
a universal trend-alignment veto for this explicitly defined reversal. Any other
analysis blocker denies. The original score is only recorded, never increased.

This establishes a possible cross-timeframe intersection, not a claim that the
same 15m structure can simultaneously be neutral and have CHoCH. The raw current
source receipt remains at C or earlier; candle prefixes end at P. No old ticker,
funding observation or receipt is created to pretend capture at P. Prefix row
hashes, counts, excluded later rows and cutoffs are recorded. Historical first
availability remains unknown, so this does not satisfy predictive MIE Gate 3.

`evaluate_sweep_history_permission` returns replayable evidence bytes. A caller
can construct such a data record, but its hash or admitted field is never an
issuer. `verify_sweep_history_permission` recomputes the entire raw-source result
and compares exact bytes. Execution/source/authentication/risk flags stay false.
No G1 quote freshness, event-consumption ledger, entry zone, SL/TP, sizing, risk,
G12, recheck or native submit is bypassed or claimed by this component.

The original requirement sources and approved design are retained outside the
repository under `validation-results/liquidity-sweep-policy-source-review-20260928`.
The new tests use explicitly synthetic raw OHLC and real analysis, not stubbed
regime/score/permission values. Root executed the first two synthetic intersection
cases; both correctly failed `prior_range_safety_denied`. The original fixture's
constant 4H/1H closes produced zero causal derivative/state confidence despite
normal wick ATR. Prior mathematical coverage was approximately 0.0503, below the
unchanged 0.35 sufficiency threshold. The first source freeze and failure receipts
remain preserved; no policy or production evaluator was weakened.

The second source freeze changes only tests and this explanation. Its synthetic
HTF closes describe a decline, a low plateau and a measured recovery that remains
below the long EMA while the short EMAs turn upward. The actual analysis must
establish neutral EMA structure and sufficient causal mathematical coverage at
the historical prefix; these are assertions, not supplied indicator values.
Short cases reflect the raw OHLC history. A separate negative test retains the
original constant-close failure. These revised cases are authored but unexecuted
at the second source freeze; root's bounded validation will establish outcomes.
Positive or negative synthetic tests are not live-market, Demo, OOS or final
acceptance.
