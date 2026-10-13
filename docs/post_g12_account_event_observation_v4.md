# Post-G12 account event observation V4

`publish_capture_inspect_history_event_v4` owns one new V3 G12/public/account
diagnostic invocation, reads its immutable receipt back, and then asks the
DB0017 qualification ledger for the exact original event key. Before the
V3 invocation can publish G12, it requires DB0039 prepublication custody;
V3 must read back that publication before any post-G12 source request. Before the
database read, V4 reopens all six G12 files through the bounded native evidence
reader. It checks the exact file set, fully decodes the five PNGs, verifies their
report manifest, matches `report.json` to V3's report digest, and checks the
original report, instrument, direction, entry, stop and target. It also reopens
the no-clobber public-only recheck sidecar under the pre-pinned directory identity
and compares its digest, event, public-packet hash, fixed geometry and observation
time to the V3 receipt. V4 pins the public root identity before V3, then
replays the bounded raw public journal under that identity and
compares its summary and packet hashes to that same V3 receipt. Missing, changed
or rehashed but inconsistent artifacts
deny the event read; previously accepted bytes are never deleted. The database
read starts after these checks and the V3 receipt's final observation, before the
original candidate deadline. V3 also pins the sealed V7 native account proof,
its readback receipt, and the original V5 history chain digest including DB
timestamps. V3 compares both companion hashes with the hashes verified by the
native runtime before accepting its later readback; the proof's clock-file hash
map must also match the files. V4 pins the account root identity before V3,
rereads the exact companion inventory and clock-file hashes under a native
directory handle, and compares the proof's current-source reference and both
companion hashes to V3.
The native runtime performed the full proof/B1 replay during V3; this later
companion check is a content and identity readback, not a new semantic replay.
V4 then calls the account journal repository's V7 ordered join, which replays
both V5 history and V7 current B1 chains under one fresh UID-locked transaction.
It compares exact capture IDs, source-reference hashes, the original history DB
timestamp digest, ordered timestamps, account scope, policy and false-authority
fields before any DB0017 event query. The B1 join receipt hash is retained in
the V4 receipt. The later DB0017 observation uses a separate UID-locked
transaction, so these are sequential readbacks, not one atomic account/event
snapshot. The checks bind the same Demo account UID and settlement currency,
report, candidate geometry, history locator, G12/public evidence and V7
account-source references. A renamed report or event cannot turn an existing
event into a new opportunity.

The result distinguishes an already recorded event from an event absent at
the measured ledger read. Absence is only a local observation; it is not a
reservation or permission to submit. Invalid, missing, late or conflicting
source/ledger evidence produces a bounded rejection code without private
account, SQL or credential content. The receipt's code-specific pin checks
prevent an incomplete path from claiming a completed observation.

V4 freezes the original candidate, policy and inputs before its first await.
After the locked account and event reads, it reopens the same pinned public
journal and samples a new native UTC validation time after journal readback.
It requires that sample to follow the DB receipt and precede the original
candidate deadline, then replays the original G1 freshness policy against the
same pinned packet. A stale packet yields
`public_g1_rejected_at_event_readback`; an unreadable or changed journal
yields `public_journal_readback_invalid_at_event_readback`. An unavailable
native clock, invalid ordering or elapsed deadline yields
`public_validation_clock_invalid_at_event_readback` before G1 runs. All remain
`DENY`. The receipt is published after this sample, so
its code describes only the check at that historical time, not current
eligibility. V4 never refreshes the quote, moves Entry/SL/TP or recomputes a
replacement candidate.

The caller provides a separate empty `event_root` before V3 starts. V4 pins
that directory and requires it to be distinct from every earlier evidence
root. Every result writes a bounded canonical `receipt.json` without replacing
an existing file. A validated ledger observation first writes its exact
canonical JSON to private `observation.json`; the receipt binds that sidecar by
SHA256. A new directory read verifies the exact file set, bytes, model,
account UID/scope/event, candidate deadline, V3 observation boundary and
recorded event state. Results without a valid observation contain only the
receipt. A late failure denies the invocation and retains every already
accepted file, including a sidecar published before a failed final receipt.

The bounded V4 receipt contains only hashes of the exact UID, original
candidate geometry and account plan, plus the event, reservation, deadline,
V3 and observation digests. It does not contain the raw UID. The private
`observation.json` can contain the raw account UID and reservation details:
keep it in access-controlled account evidence storage and exclude or redact it
from public reports and final release bundles. Export only the hashes and
explicitly redacted audit summaries, never the raw sidecar.

Every V4 result is `DENY`. The original G1–G11 run is still caller-origin,
the V5 history tail and PortfolioRiskSnapshot are incomplete, and the
post-G12 result is public-only. V4 neither reserves risk nor creates a durable
intent, Arm state or order. Demo and Live transport hard denial remains in
force. The public journal replay checks retained bytes and semantics; it does
not establish a new source-owned capture or current execution inputs. V4 does
not independently recompute G12's computational evaluation hash.
The sidecar's post-G12 12-gate candidate digest is pinned by its receipt SHA,
but cannot be recomputed from V4's original 11-gate candidate alone. These
replay gaps require a separately reviewed source-owned handoff before any risk
reservation. Synthetic unit tests exercise event/UID/deadline/receipt conflicts,
late artifact mutation, account-root/B1 reference and timestamp mutation, and
no-clobber readback. The isolated PostgreSQL DB0017 integration uses a synthetic
B1 readback. A separate isolated test uses real V5 and V7 B1 database chains
at the V4 account readback helper, with synthetic HTTP and V3 timing inputs.
Neither test proves a complete source-owned invocation; matching CI must run
both before their database behavior can be credited.
