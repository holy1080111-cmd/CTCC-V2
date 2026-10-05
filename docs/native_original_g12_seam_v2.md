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

This handoff deliberately stops before G12. A native initial public packet alone
cannot create the complete original candidate: the current V2 precursor also
requires captured Demo instrument metadata, while full G1–G11 qualification,
account-specific costs, and source-derived protection are not yet owned by the
same invocation. Calling the existing G12 publisher with a caller-supplied run or
candidate would recreate the original ownership gap. Accordingly, the successful
diagnostic code is `native_original_v2_candidate_source_required` and all
admission, source-verified, G12, post-publication, recheck, account, reservation,
execution, and order flags remain false. The older
`publish_capture_public_v2` remains a separate caller-origin diagnostic and is
not promoted by this handoff.

The focused synthetic unit tests check the native handoff, forbidden caller
replay inputs, foreign-task consumption, final expiry, cancellation, and the
absence of G12/post-publication calls. They do not establish production source
authenticity, an accepted candidate, Demo order eligibility, or Live authority.
