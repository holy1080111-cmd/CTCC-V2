# Canonical HighVol/Momentum qualification v1

The native Demo pipeline has an explicit opt-in HighVol permission contract in
`app/trade_qualification/high_vol_momentum_qualification_v1.py`. It uses the
existing `breakout_continuation` and `volatility_expansion` families. The other
six strategy families retain their existing permissions. It creates no separate
trading engine, account authority, risk allocator, evidence publisher or order
route. Live does not consume this Demo profile.

`CTCC_DEMO_HIGH_VOL_MOMENTUM_V2` defaults to false. Selecting it changes the
frozen configured policy identity, original precursor policy SHA and original
G1–G4 prefix policy ID. The unchanged legacy regime router still reports
`High Volatility / NO_TRADE`. A passed G2 reports that actual regime and its
original veto together with the separate versioned permission proof. No
`Unknown` or HighVol state is relabelled as Trend or Expansion.

The proof independently rebuilds analysis from the raw market source and checks
its exact source digest. All four frames must have at least 200 contiguous
confirmed bars and the current closed tail. It requires 4H direction, a matching
or neutral 1H, no opposed frame, directional 15m BOS, no extreme frame, known
causal states with shock strictly below 0.65, no analysis blocker, and confirmed
existing mathematical direction. The normal condition scores and minimum score
still apply. Expansion also replays the original compression, swing break,
subsequent 5m momentum transition, event key, setup age and original expiry.

The genuine configured source owner chooses the profile before constructing a
candidate. Both original precursor adapters use distinct profile-bound policy
digests. G2–G4 independently recompute the raw permission and ordinary family
conditions; the shared trigger, zone, protection, timing, economics, portfolio
risk and G12 path continue normally. The G12 artifacts bind the qualification
and policy digests. A supplied receipt, `admitted=true`, Settings value or pure
permission object cannot construct a native source owner.

After publication, R7 uses only the original frozen policy pin and newly
captured current public/account data. It recomputes G1 and HighVol G2–G4, then
the existing original-event survival/path, original zone, fixed entry/SL/TP,
economics and current portfolio checks. It does not extract a replacement event
in the current HighVol permission helper. Changed source, expired history,
unknown causal state or a new shock rejects the original candidate. Existing
reservation, durable intent and final submit boundaries remain mandatory.

The structural per-trade cap remains 0.5%, portfolio stop risk 1%, margin bucket
300 USDT and aggregate margin cap 60%. No score bonus, risk increase, geometry
repair or expiry extension is introduced. A favorable later market cannot repair
an original failure. Previously published receipts do not acquire this profile.

Deployment must use the canonical exact-source package and its normal identity
validation. Profile changes require stopped/disarmed operation, verified zero
positions, normal/algo orders, tracked or uncertain exposure, reservations and
unresolved intents, followed by the existing policy initialization and explicit
re-Arm. Changing the flag in a held invocation invalidates its source/config and
original local policy replay. No rollout closes a position, cancels protection
or changes existing leverage. The September overlay installer remains disabled:
it cannot patch an old image into this new contract.

The focused tests use declared synthetic source bytes and real calculations.
Their positive mathematical cases do not constitute real market evidence,
authenticated Demo acceptance, Gate3, predictive edge or Live acceptance.
The original observation v2 and failed-G2 sidecar remain DATA diagnostics.
