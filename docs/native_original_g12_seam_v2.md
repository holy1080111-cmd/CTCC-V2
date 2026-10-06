# Native original public handoff before G12

## Frozen Demo public-route policy (offline only)

`demo_public_origin_policy_v2.freeze_demo_public_origin_policy_v2` now records
the exact reviewed regional REST/WS origins, TLS hostnames, GET path inventory,
role-specific headers and `x-simulated-trading: 1` as bounded canonical V2 bytes.
The [OKX API guide](https://www.okx.com/docs-v5/) documents the regional
Demo domains and simulated-trading header; its
[2026-09-30 WebSocket change](https://www.okx.com/docs-v5/log_en/) says port
443 already works and port 8443 will stop accepting connections on
2026-10-31. The policy pins explicit `:443` endpoints.
Its replay requires an independently selected expected region and rejects changed
origins, Production WS, omitted simulated-trading headers, noncanonical bytes and
caller claims of authenticated source, account, or execution authority. The V2
table is frozen separately from later route policy so old V2 policy bytes remain
replayable after a future route revision; a changed current route cannot issue a
new V2 record. Existing public capture packets and receipts are unchanged.

This is a route **policy** only. It observes no credential, network request,
response or account registration; a caller can choose a reviewed region and
produce matching policy bytes. The native V2 Demo public-source pre-I/O hard DENY
and both Demo/Live order hard DENYs remain in force. Matching policy bytes do not
unlock G1, G12, recheck, reservation, intent, or execution.

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

## Requirements before operational G1 registration

The current repository has no eligible calibration source for a non-synthetic
policy. The retained October 2026 BTC public replay explicitly records
`original_source_verified=false` and `measured_availability_eligible=false`;
it is a diagnostic packet, not a trusted Demo observation. The current native V2
plan also pins the production public WS origin while labeling the capture Demo.
That origin mismatch must be corrected and verified with a region-bound Demo
transport before any new native sample can support operational policy review.
The synthetic fixture policy in `full_public_numeric_v2_profile.py` cannot be
copied or registered as the production policy, even if its digest matches.

Registration requires a reviewed, immutable policy record prepared before the
qualifying evaluation. It must pin the exact trusted source profile and region,
the analysis implementation/source identity and `analysis_version`, and every
`DataQualificationPolicy` field: at least 200 confirmed bars per 4H, 1H, 15m,
and 5m timeframe; snapshot, ticker/mark, and independent WS-reference ages;
candle-tail age; reference conflict; mark dislocation; spread; and absolute
funding bounds. Each selected numerical limit needs a documented operational
basis and failure rule from origin-verified, causally timed captures of the
intended instruments and market conditions. The model's permitted ranges are
input validation limits, not approved operating thresholds. No result from an
already viewed diagnostic packet may be used to tune and then claim a sealed
out-of-sample policy.

After sealing, native initial capture must retain and replay the exact public
raw bytes and receipt chain, verify exchange and host clock ordering, and
evaluate G1 under the pinned policy in that same invocation. Tests must reject
missing, stale, malformed, conflicted, or wrong-environment components and
changed policy/version pins. Independent replay of the exact G1 result is
required. A G1 PASS would remain a data-consistency result, not original-source
verification, G12 completion, reservation, Demo submission, or Live permission.
Until those prerequisites exist, this native entry continues to return the
bounded `native_g1_policy_unregistered` DENY receipt.

The focused synthetic unit tests check the native handoff, canonical bounded
policy-gate receipt, forbidden fixture G1/G12/downstream calls, malformed and
stale source rejection, forbidden caller replay inputs, foreign-task
consumption, final expiry and cancellation. They do not establish production
source authenticity, G1 PASS, an accepted candidate, Demo order eligibility,
or Live authority.
