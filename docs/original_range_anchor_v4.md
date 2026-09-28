# Original range anchor qualification V4

This opt-in computational profile is `ctcc-original-range-anchor-v1`. Its prefix,
qualification result, pre-evidence policy/run, evidence run and current-condition
record require explicit V4 contract markers. The profile marker is also required
on the prefix policy and qualification result. V1, V2 and V3 models retain their
existing fields, defaults and semantics. No runtime flag enables trading.

G1–G6 replay the original raw market source using the unchanged range regime,
neutral 4H/1H permission, strategy predicates, closed 15m range-edge reclaim,
closed 5m momentum transition and timing policy. No higher score repairs a failed
required condition. No old result, receipt or caller PASS flag grants permission.

G7 adds the integrated source's restrictive original-anchor rule:

- Long: original source zone intersected with `(original level, level + setup ATR]`.
- Short: original source zone intersected with `[original level - setup ATR, original level)`.

Both endpoints are rounded inward to the real instrument tick. Anchor equality
is forbidden. The outer bound is inclusive. A band with no executable tick is
rejected. The confirmed setup ATR can differ from the earlier ATR used to form
the original zone; their intersection never enlarges either band. The source
level must have been known before the setup close. The original invalidation,
source identity, event, creation time and expiry remain unchanged. The candidate
entry is never repriced, and an improved quote cannot repair a candidate already
outside the original permitted zone.

`build_original_range_anchor_zone` in `location.py` provides this pure shared
rule. Its result is source consistency evidence only. The prefix replays the
raw source first and binds the rule, original event key, source digest and
narrowed zone digest in G7. The V4 prefix model independently reconstructs the
same band when reading a stored record. Evidence preparation and readback use
the same builder by exact result version. Current G1–G4 retain the original
intent and policy; their record cannot replace a trigger, zone, SL or TP.
The enclosing recorded recheck retains the original narrowed zone for the
new executable quote.

G8 currently retains the original `source_data_blockers` rejection for neutral
range analysis with `multi_timeframe_not_aligned`. This change does not apply
the reversal alignment selector to range or remove that diagnostic. The full
integrated `range_alignment_permitted` policy still needs source verification
before claiming an exact port of that legacy helper. This is not a requirement
that prohibits a new explicitly versioned policy derived from the reviewed
canonical strategy predicates; see `range_protection_v5.md`. V4 itself retains
its historical denial and wire format.
The retained installer patch contains only its call site and is insufficient to
reconstruct the complete rule. Expected integrated source SHA-256:
`6ce7a0252421c080bcc4f977b27b6eb22d3485fd4e2d8d6a2c3415f39936b0f1`.

Consequently, synthetic range G1–G7 passes currently stop at G8. G12 publication
must not begin for them. No positive G12/R7/reservation/intent/execution acceptance
is claimed for V4. Downstream type dispatch is preparation for the same shared
chain; an allowlist entry is not authority. The shared reservation preparation
used by both reserve and consume now rebuilds the original zone and checks the
current executable quote before risk/cap computation. Even a passing location
is rejected with `ledger_range_policy_incomplete`: this profile lacks verified
complete range protection permission, and a reservation request alone does not
contain the original quote/reference documents needed for full source replay.

The intent parser admits only explicit V4 policy/prefix/profile markers. V4
cannot select the old intent-v1 path by omitting an execution binding. It must
use the full intent-v2 original/current source and account replay, which retains
the authentic G8 denial. The same original-zone restriction is also checked
before deriving a bound FOK order, whose price must remain the exact reserved
executable sample. Existing legacy/V2/V3 wire formats are unchanged. No positive
V4 reservation or intent acceptance is claimed. Existing shared entry transport
remains denied. No exchange, credential, account, Notion or order IO is performed
by this profile.

Synthetic tests cover both directions, strict and non-grid anchors, inclusive
outer bounds, one tick outside, differing ATR observations, no legal tick,
missing/malformed/future basis, duplicate basis fields, callback rejection,
wrong/future/stale/crossed quotes, original candidate failure, replayed source
changes, candidate/event/expiry tampering, required nested version markers,
source-bound evidence snapshots and denial before G12 publication. These are
engineering tests, not real Shadow/Demo evidence or predictive/OOS claims.
