# Owned original-source handoff V2

`capture_owned_original_sources_v2` is a bounded, read-only handoff intended
for the original candidate phase. It accepts a reviewed instrument and market
policy, an unused controlled Demo account session, configured DB access, and
separate evidence roots. It does not accept caller-supplied market packets,
gate results, report IDs, clocks, or a publication barrier.

The coordinator attempts native V2 public capture, consumes its one-use carrier
in the same task, and only then attempts native account capture. It checks the
public packet and the account diagnostic against the controlled plan, region,
source digests, readback receipt, task clock, and original lease. Evidence roots
must be local, disjoint, and free of symlink/junction ancestors. A failed or
cancelled attempt consumes the account session; no source or order request is
retried by this handoff.

The returned receipt is always `DENY`. It proves no G1–G11 calculation, G12
publication, post-G12 recheck, complete account, reservation, intent, or order
eligibility. The current native V2 public issuer still selects a Production
socket for its Demo-labelled plan and refuses capture before network I/O, so
the real path currently reports `original_source_public_unavailable`. Synthetic
join tests check only the handoff mechanism. A reviewed account-bound Demo
public origin issuer and full source-derived gate chain remain prerequisites
before this handoff can participate in a qualified candidate flow.
