# Native original public handoff before G12

`capture_native_original_for_g12_v2` is a diagnostic-only entry in the post-G12
runtime module. It accepts an empty initial capture root, one reviewed instrument,
and the fixed V2 public collection policy. It accepts no market snapshot, run,
candidate, old receipt, barrier, PASS flag, callback, or credentials. In one task,
it creates a new initial-public native capture, consumes its private one-use
carrier, checks the initial stage/report/instrument and null publication barrier,
and rebuilds the V2 context from its raw packet at a final native clock reading.
Cross-task use, expiry, or a changed lineage returns a fixed denial; cancellation
propagates. The result exposes only the initial report and source/journal hashes,
not a reusable packet or capability.

This handoff deliberately stops before G1 and G12. The only fixed G1 data
policy currently in the source is part of the explicitly synthetic numeric
profile. No native operational G1 policy is registered, so the runtime does not
borrow the fixture's thresholds or accept a caller-provided policy. After a
valid same-task source readback, it returns
`native_original_v2_g1_policy_unregistered` and a bounded canonical
`ctcc.native_initial_g1_policy_gate.v1` receipt containing the original public
packet/journal hashes, native observation time, null policy/evaluation hashes,
and explicit `g1_evaluated=false`, `g1_passed=false`, `DENY`. Its SHA-256 is a
local receipt identity, never a permission. Malformed, stale, expired or
foreign-task captures return a denial without this receipt.

An operational G1 policy must be reviewed and registered separately before the
same-task carrier may drive `data_v2.evaluate_public_market_data_v2` and its
verifier. Even then, a native initial public packet alone cannot create the
complete original candidate: the V2 precursor also requires captured Demo
instrument metadata, while full G1–G11 qualification, account-specific costs,
and source-derived protection are not yet owned by the same invocation. Calling
the existing G12 publisher with a caller-supplied run or candidate would
recreate the original ownership gap. All admission, source-verified, G12,
post-publication, recheck, account, reservation, execution, and order flags
remain false. The older `publish_capture_public_v2` remains a separate
caller-origin diagnostic and is not promoted by this handoff.

The focused synthetic unit tests check the native handoff, canonical bounded
policy-gate receipt, forbidden fixture G1/G12/downstream calls, malformed and
stale source rejection, forbidden caller replay inputs, foreign-task
consumption, final expiry and cancellation. They do not establish production
source authenticity, G1 PASS, an accepted candidate, Demo order eligibility,
or Live authority.
