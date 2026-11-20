# HighVol/Momentum source observation v2

`app/trade_qualification/high_vol_momentum_observation.py` is a narrow,
default-off Demo diagnostic. It replays the four exact closed OHLC timeframes,
the original event key and source SHA256, the fixed strategy timing policy, 15m
BOS, 4H/1H direction, 15m/5m non-opposition, measured causal shock, and
mathematical direction. For volatility expansion, event extraction requires
actual prior compression before the break and a later 5m momentum transition.
The policy digest is fixed, and the supplied original-source pin must name the
current canonical original-candidate precursor policy. Each observation records
its exact analysis version for replay review. The original-source pin also binds
the complete canonical timing-policy digest, its TTL and setup-age values, exact
original setup/trigger/expiry times, and the original candidate deadline.
The effective expiry is the earliest of the original trigger expiry, original
candidate deadline, and current timing-policy expiry. Any policy drift rejects
the observation, even if the new policy is more permissive. A supplied pin does
not prove an original candidate existed.

Every observation has `admission="DENY"`, `execution_authority=false`,
`qualification_performed=false`, and `source_authenticity_verified=false`.
Even `source_conditions_met=true` would be a computational observation only.
The existing regime router still returns `NO_TRADE` for High Volatility. No
execution path consumes this diagnostic receipt as permission. The separate
[canonical qualification v1](high_vol_momentum_qualification_v1.md) now rebuilds
its own raw-source proof inside the genuine original and post-G12 Demo chain;
it uses distinct profile/policy identities and retains every common gate.
This observation object itself cannot make HighVol tradable.

The separate versioned qualification contract binds the original candidate,
strategy profile, event, entry, stop, target, expiry, source, and policy digest;
recomputes the same conditions on post-G12 fresh data; and retains every other
gate and common final submit boundary. A favorable new event or quote cannot
repair an original failure. The historical installer remains disabled because
it patches an old image and its flat check omits durable reservations,
unresolved intents, and local uncertain state.

The focused synthetic tests prove default-off behavior, unchanged legacy
routing, source-derived event chronology, policy/source/event/timing pin
rejection, expiry shortening, future-source rejection, and permanent lack of
execution authority. Those
fixtures are neither real market evidence nor Demo acceptance. The present
synthetic HighVol examples are blocked by missing breakout HTF support or a
measured 15m shock; no positive production permission is claimed.
