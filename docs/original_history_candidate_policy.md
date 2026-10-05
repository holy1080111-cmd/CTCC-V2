# Original history candidate precursor v1

This additive pure adapter derives a diagnostic original intent from the exact
retained public packet and account instrument metadata. It changes no base
precursor, historical engine, wire contract, reservation or submission route.
Every result is DENY. A derived intent remains WAIT for the owned producer,
complete qualification, event ledger, sizing, protection, G12 and fresh recheck.

| Strategy | Exact existing engine contract | Reused policy |
| --- | --- | --- |
| structure_reversal | ctcc-history-pre-evidence-v3 | reversal history protection v1 |
| volatility_expansion | ctcc-history-pre-evidence-v2 | expansion HTF permission v1 |
| range_reversal | ctcc-history-pre-evidence-v5 | neutral range protection v1 and original range anchor v1 |
| liquidity_sweep_reversal | ctcc-history-pre-evidence-v1 | original history observation only; sweep_htf_policy_unspecified remains WAIT |

The other four strategies retain their existing base precursor. Wrong contract
or strategy combinations never fall back, change direction or select a different
event. The strategy/profile is a declared frozen input; its caller supplied hash
is an integrity comparison, never native source ownership or an issuer.

`derive_history_candidate_precursor` accepts the public and account packets,
exact source/policy pins, strategy/engine contract, creation cutoff and existing
service deadline. There are no direction, entry, analysis, event, score, quantity,
SL/TP or expiry overrides. Instrument tick, lot, contract and UID/session lineage
come from the existing raw account instrument helper. This does not establish a
complete current portfolio or account freshness.

The adapter replays public raw source, actual G1, analysis, route, predicates,
history admission and the correct version's current permission. Structure and
expansion use the same source-derived historical admission and original event
extractor as their engines. Range replays its original permission and uses
`build_original_range_anchor_zone`; it does not substitute the base range zone.
Unknown route labels are preserved. Sweep has no invented HTF permission.

The entry is the original captured ask for long or bid for short and must lie
exactly on the instrument tick. No rounding or clamp occurs. The unchanged event
and zone must contain this entry. Expiry is the minimum of original event expiry,
fixed strategy TTL and existing service deadline. Later replay cannot renew it.
An empty consumed-event set is never invented: no full G6 or G1–G11 PASS is
claimed. Spread, costs, complete risk, SL/TP and economics remain full-engine
obligations; these preliminary predicates cannot bypass them.

## Current historical data versus predictive availability

The collector's original raw pages are retained and replayed with exact ordering,
counts, gaps, duplicates, confirmations and coverage. The precursor records every
raw row's canonical hash, original page hash/index, request/header/body times,
open/close time and confirmation. All required closed rows and complete receipts
must precede the declared current decision cutoff. The confirmed tail must equal
the applicable timeframe boundary. This supports using currently received closed
history for a current decision; it does not prove native clock or HTTP ownership.

The existing event algorithms rebuild setups, prior trend, pivots and false-to-
true momentum transitions from closed prefixes. Separate setup, trigger and pivot
prefix witnesses record eligible raw rows at each candle-close cutoff. They do
not claim that this later HTTP capture existed at those historical times.
Historical first availability remains null and predictive point-in-time proof
remains false. Historical research still requires its distinct immutable first-
availability provenance; these current-decision witnesses cannot satisfy Gate 3.
Raw source timestamps and measured receipt fields are never overwritten.

## Validation state

The new tests use synthetic raw HTTP/socket transports with the real collectors,
G1, analysis, predicates and historical evaluators. They compare the precursor
event/zone to each exact historical G1–G7 engine, verify original expiry and raw
timeline bindings, and retain sweep WAIT and malformed/source/version denials.
Source authoring and lint are separate from behavioral acceptance. At the initial
freeze these tests have not run; no exchange, account, qualification or trading
acceptance is claimed. The root validation record supplies actual test outcomes.
