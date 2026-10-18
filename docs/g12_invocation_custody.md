# G12 invocation custody V1 — diagnostic only

Migration `0039` adds append-only `g12_invocation_claims` and
`g12_invocation_publications`. They are separate from DB0017's risk reservation
and durable submission intent. Both records are always `DENY` and cannot be
converted into order permission.

An exact `G12InvocationCustodyRepository` is now required on the existing
`post_g12_public_runtime` V2 path, V2 account join, V3 account-history join, or
V4 event observation (through V3). It commits one preclaim **before** that invocation publishes
G12. The claim pins the exact Demo account UID hash and account-plan hash,
original event, report, instrument, direction, entry, SL, TP, earliest deadline,
the already replayed G1–G11 run hash, original market and input-envelope hashes,
and native evidence-root identity. It retains no raw account UID, credentials,
market packet or full input values. The repository independently rereads the
committed row in another session; an uncertain commit or mismatch stops before
G12. The root and input hashes are checked again after the database await.

Only a **new** six-file G12 `written` receipt with the same run, event, fixed
geometry, deadline and root can add a publication row. The row pins all six
file hashes, report/evaluation hashes and completion barrier. It commits and is
read in a new session before any post-G12 public/account capture begins. The
database rejects a completion timestamp outside the strict claim-to-database
recording interval; clock disagreement denies the publication. The initial
publication check and the final post-database check each rehash all six exact
files while holding one native evidence-root context and comparing its identity
before and after the read. After the committed readback, the same invocation
also recomputes G1–G11 and G12, rerenders the fixed liquidity-v2 profile, and
compares all six rendered files byte-for-byte with the native published files
before any post-G12 public or account request. It rechecks the original input
hashes both after the database await and after rendering. A computation or
input mismatch denies continuation without removing accepted evidence. A
failed publication insert/readback or crash retains the preclaim and any
already-written evidence; there is no retry, overwrite or reconstruction of
the old attempt into a new one. The account-UID/event unique constraint permits
at most one preclaim across report IDs. A restart may read committed rows for
forensics but cannot resume the invocation or dispatch an order.

The schema's immutable triggers reject update, delete and truncate, and check
the record hashes, event/claim links and fixed false-authority fields. The
repository performs separate transaction/session readbacks. A missing or
wrong-type repository now refuses before G12 publication; a missing committed
publication refuses before post-G12 capture. The older caller-origin
`one_shot.publish_capture_recheck(purpose="observed")` is denied before G12
because it has no DB0039 custody dependency. Its explicit `synthetic_test`
mode remains for offline regression only. Production role
grants, backup/WAL recovery, database/host clock agreement and migration
round-trip acceptance still require deployment validation. The existing Demo
public-origin pre-I/O denial runs before a DB preclaim; this change does
not bypass that denial.

This is **hash custody, not full source custody**. The original G1–G11 run and
inputs still come from the caller, and their values are not stored by this
journal. A hash alone cannot reconstruct them after a process crash or prove
their source. A writer with the database credential could insert fabricated
diagnostic hashes; neither SQL nor synthetic tests authenticate the exchange.
The V1 publication row records chronological matching under the reviewed
repository, but its `publication_provenance_verified=false` deliberately remains
false until an independently protected service/deployment boundary is verified.
The operational G1 policy, trusted original source, complete V7/B1 history,
PortfolioRiskSnapshot, current cost/recheck, atomic reservation, intent and
final dispatch authority remain unfinished. All Demo and Live order writes
stay disabled.

Unit tests cover original pinning, six-file new-write binding, root changes,
old receipts, identity mismatches and denial before fresh capture. Isolated PostgreSQL integration checks
commit/readback/restart audit, concurrent same-event uniqueness and immutable
row guards, including malformed direction and chronology. Its fabricated hashes are database fixtures, never real G12, Shadow
or Demo evidence. No PostgreSQL result is claimed until that test actually runs
against the exact source migration head.
