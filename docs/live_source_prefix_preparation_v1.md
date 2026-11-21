# Immutable source preparation before the final current capture

The Live bridge prepares only already-captured history and baseline metadata
before its existing final current-account GET. Incremental retained-partition
selection uses the original plan and previous history; it does not depend on
the new current result, time, account configuration or balance revision.

The local prepared value contains page count, original raw-byte count and a
tuple of canonical row bytes. It contains no owner, permit or eligibility result.
Row order and duplicate rows are preserved. Encoding each row inside a singleton
outer array retains the original canonical nesting-depth checks; combining the
row bytes produces the same complete bank JSON and SHA as the original path.

The final GET, its actual measured completion, current custody replay, balance
revision comparison and source freshness remain unchanged. After the current
suffix is added, the existing full source identity replay still parses and
validates every history, retained, baseline and current page. A prefix changed
while the final GET is pending cannot escape that replay, even when its own raw
and canonical digests were updated consistently.

No endpoint is reordered, retried or refreshed. No timestamp, original candidate,
policy, authority expiry or five-second bound is extended. Broader financial-row
borrowing is excluded. This change does not establish configured runtime or
venue acceptance; the prior cost-only loopback's account source is synthetic
and does not measure this production capture seam.

The declared DATA microcost at `N319/N423G2_PREFIX_PREP_MICROCOST_RESULT.json`
measured a single builder scan shifting before a symbolic final-current boundary.
For 64 synthetic pages (1.66 MB), modeled builder plus one unchanged full replay
fell from 100.6 ms to 49.0 ms; for 256 pages (6.74 MB), 410.2 ms to 205.7 ms.
These are corpus-specific component results, not account workloads or end-to-end
deadline acceptance. All additional source replays remain mandatory.

The focused validation passed 97 tests: 19 new preparation/producer cases,
60 existing source-ledger cases, five incremental cases and 13 financial-row
cases. Both original producer functions were exercised with declared synthetic
GET/config/lease ingress. Consistent prefix mutation at the final GET return was
rejected by the unpatched full source replay. Six separate genuine q-source
guard canaries rejected replacement bindings and changed code for all three
new helpers before any foreign callback executed. These tests do not establish
trusted transport, account, database or venue acceptance.
