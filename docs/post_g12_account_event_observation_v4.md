# Post-G12 account event observation V4

`publish_capture_inspect_history_event_v4` owns one new V3 G12/public/account
diagnostic invocation, reads its immutable receipt back, and then asks the
DB0017 qualification ledger for the exact original event key. Before the
database read, V4 reopens all six G12 files through the bounded native evidence
reader. It checks the exact file set, fully decodes the five PNGs, verifies their
report manifest, matches `report.json` to V3's report digest, and checks the
original report, instrument, direction, entry, stop and target. It also reopens
the no-clobber public-only recheck sidecar under the pre-pinned directory identity
and compares its digest, event, public-packet hash, fixed geometry and observation
time to the V3 receipt. Missing, changed or rehashed but inconsistent artifacts
deny the event read; previously accepted bytes are never deleted. The database
read starts after these checks and the V3 receipt's final observation, before the
original candidate deadline. It binds the same Demo account UID and settlement currency,
report, candidate geometry, history locator, G12/public evidence and V7
account-source references. A renamed report or event cannot turn an existing
event into a new opportunity.

The result distinguishes an already recorded event from an event absent at
the measured ledger read. Absence is only a local observation; it is not a
reservation or permission to submit. Invalid, missing, late or conflicting
source/ledger evidence produces a bounded rejection code without private
account, SQL or credential content. The receipt's code-specific pin checks
prevent an incomplete path from claiming a completed observation.

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
force. This late readback does not independently replay the public raw journal,
the V5/V7 account database chains, or G12's computational evaluation hash.
The sidecar's post-G12 12-gate candidate digest is pinned by its receipt SHA,
but cannot be recomputed from V4's original 11-gate candidate alone. These
replay gaps require a separately reviewed source-owned handoff before any risk
reservation. Synthetic unit tests exercise event/UID/deadline/receipt conflicts,
late artifact mutation and no-clobber readback. The isolated PostgreSQL
integration case must run in matching CI before its database behavior can be
credited.
