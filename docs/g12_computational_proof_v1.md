# G12 computational proof V1 (diagnostic only)

`publish_g12_computational_proof_v1` accepts an original G1–G11 run, the raw
market and its exact original input envelope. It first uses the existing
versioned G1–G11 replay and publishes a **new** G12 six-file packet. Only a
passing, fresh `written` receipt may then create `proof.json` in a separate,
pre-existing, empty service-owned directory. Both directories use the existing
native no-clobber publisher and bounded readback. A failed or interrupted late
proof step can leave the six G12 files or a partial proof; neither is repaired,
overwritten, or promoted by retry. An old receipt or already-present report
cannot enter this **publish** path as a fresh G1–G11 run.

The sidecar contains only the full `EvidenceGateRun` SHA-256, its G1–G11 and
source digests, six file hashes, publication times and renderer profile, with
`admission=DENY`. It does **not** save the full run, risk inputs, account values,
UID, credentials or raw market data. `read_g12_computational_proof_v1` therefore
requires the complete original market, original input envelope and full G12 run
from a separately retained trusted source. It replays G1–G11, rebuilds the
snapshot and G12 gate, rerenders the selected profile, reopens the exact six
files, and compares all six byte-for-byte. A matching sidecar digest alone is
insufficient. This V1 record by itself cannot reconstruct the calculation after
a process restart; durable private custody of the full inputs and run remains
an integration requirement.

The separate readback accepts an expected digest supplied by its caller. A
caller who controls both an old G12 packet and the sidecar directory can create
a matching sidecar later. Readback therefore proves only that the supplied
calculation and six files agree; it **cannot** prove that the sidecar was
created during the original publication. Its explicit
`publication_provenance_verified=False` and `admission=DENY` remain fixed even
when all bytes match. Fresh-publication authority requires an independent,
trusted invocation journal bound before G12 publication, which this slice does
not implement.

The current caller-origin diagnostic does not prove original source custody,
account authenticity, point-in-time availability, post-publication freshness,
post-G12 continuation, reservation or execution eligibility. It is not wired to
the older V3/V4 account receipts or an order route, and it cannot qualify an
older G12 receipt retroactively for trading. Its proof only closes the local computational
and exact-file comparison gap **when all replay inputs are independently
available**. The six-file report still deliberately contains the pre-G12
snapshot; the separate sidecar avoids a circular report/G12 hash.

Focused synthetic tests cover V1 and liquidity V2 rendering, passing history
run versions V2/V3/V5/V6, the existing blocked V4 case, changed original inputs,
tampered report/sidecar, and no-clobber retry. They do not count as a real
Shadow/Demo sample or production G12/R7 acceptance. Windows publication uses
file fsync and readback but does not promise crash-atomic directory metadata;
the existing native-storage limitations apply.
