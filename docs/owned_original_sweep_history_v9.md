# Owned original sweep history V9

The V2 raw original precursor already replays `ctcc-sweep-history-protection-v1`
for `liquidity_sweep_reversal` and retains the exact original event, zone, entry
and expiry. It deliberately returns `WAIT`/`DENY` because the sweep Stage C
native qualification and later safety gates are not owned there. The older V1
precursor and its `sweep_htf_policy_unspecified` result remain unchanged.

`preflight_owned_sweep_history_v9` adds a distinct, read-only native inspection.
It accepts source roots, instrument, policy and a controlled account session;
there are no caller event, candidate, score, G12 receipt or PASS parameters.
Within the existing one-use original-source invocation, the coordinator keeps
the exact public/account raw packets private, replays the precursor, then
independently recomputes raw G1 and verifies the V6 sweep permission against the
original precursor's complete history receipt. It binds the public and account
packet hashes, source hash, policy hash, permission receipt hash, original event
key, optional original intent and unchanged event expiry. A mismatch or expired
one-use source returns no inspected history.

The returned V9 receipt contains only bounded hashes and static denial codes.
An admitted V6 history with a derived precursor intent is
`sweep_history_observed_wait_pit`; a good history with a cancelled original
entry is `sweep_history_observed_no_intent`. Rejected or unreached histories
remain denied. Neither result creates or repairs an entry, SL, TP, event, zone,
score, expiry or account assertion. The actual snapshot route remains Unknown;
the sweep's V6 history classification is not a Trend label.

The original raw candle packet is observed at the current capture. It cannot
prove when those rows first became available in the past. The V9 receipt fixes
`historical_first_availability_verified=false`, and every publication,
qualification, account-risk, reservation and execution authority field is
false. The current native public origin can still refuse capture before network
I/O. Synthetic tests of the private evaluator and receipt readback establish
lineage mechanics only, not a real Demo source, MIE Gate 3, Shadow or an order
permit. A future source-owned V6 G1–G12/portfolio implementation requires a
new reviewed contract; this V9 record cannot resume into it.
