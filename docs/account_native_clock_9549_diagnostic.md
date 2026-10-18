# Signed Windows 9549 account-clock diagnostic

Windows `w32tm.exe` version `10.0.26100.9549` has its own exact signed-file
profile in `app.domain.native_clock`. This document defines only the new
**DENY-only V6 exposed-account** native companion contract. It does not change
the historical 9278 policy bytes, hashes or replay rules.

The exposed-account runtime first requires a controlled V6 Demo session and
claims one initial stage. Its `host-before.json` is acquired by that stage's
native observer, published without clobbering and read back. Only then may
`contract_for_exposed_v6_owned_before` choose the new 9549 proof and policy.
The choice comes from the accepted original host observation; no API input,
plan field, environment variable or proof document can choose the profile.
The new replay checks that both original host observations have the same exact
9549 profile and still verifies the raw probes, timestamps, source phase chain,
original account journal and separate storage readback. A 9278/9549 mixture,
unknown signed resource, profile relabel, altered policy digest or missing
original witness is rejected. Neither timestamp tolerance nor exchange
timestamps change.

The sealed V6 current-source verifier always records
`algo_type_coverage_incomplete` because that plan queried four, rather than
eight, independent algo types. When original current rows also show exposure,
it records `current_exposure_requires_protection_and_local_join`. The 9549
exposed policy requires **exactly both** blockers, observed exposure rows and
`observed_flat=false`. Any other blocker or a flat source is rejected. This
new policy does not edit the historical 9278 V4 policy digest or make the V6
inventory complete.

The new exposed observation carries a distinct policy hash linking it to the
new native companion policy. Its `account_complete`, `flat_start_permission`,
`source_authenticity_verified` and `execution_authority` remain false;
`admission` remains `DENY`, and it has no snapshot, owner, raw-packet lease,
reservation or order permission. The V7 flat current-use revocation remains in
force before any account GET. Other account-native contracts still require
their historical 9278 profile; they are not implicitly migrated to 9549.

Pure synthetic tests build a 9549 initial stage, replay its exposure source,
seal an immutable memory-backed proof and open a separate readback handle;
mixed/wrong-profile and flat-source cases are rejected. They do not establish an
authenticated OKX Demo capture, registration region, complete account history,
portfolio risk, post-G12 recheck or execution eligibility. In particular,
DB0043's source witness remains DENY-only and is not promoted by this clock
contract.
