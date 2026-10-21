# Native current Demo account source V4

`acquire_initial_demo_account_source_v4` earns one current source owner through
native acquisition and exact durable readback. It does not accept a packet,
source receipt, clock callback, HTTP client, trusted flag or caller balance.

The caller opens `OkxDemoPrivateRestClient.pinned_read_session`, creates a new
`CurrentDemoAccountCapturePlanV7` bound to that lease, and constructs
`ControlledDemoAccountSession.from_pinned_read_session`. A public V4 owner using
that lease must already have been consumed; use a new account session and plan
pin. Reusing or weakening a public owner's `_used=False` check is prohibited.

Two distinct, nonnested, empty private directories are required:
`private_proof_root` holds the signed config attempt/proof, while
`native_proof_root` holds the existing exact native-clock companion inventory.
Account identifiers and original raw pages belong only in private custody.
Credential values, signatures and private headers are never stored.

The issuer verifies the native configured database factory and the restricted
DB0046 custody deployment before private exchange I/O. A signed, native-TLS
config route lease is consumed before claiming the one-use account session.
The existing V7/9549 coordinator then acquires the complete original B1 page
chain, host/exchange clocks and separately reopened native companion. Its old
diagnostic, policies, hashes and V7 flat-use refusal remain unchanged.

The new owner independently checks the still-live native stage and journal
owner against the exact original chain head, packet digest and companion pins.
The private bundle includes all B1 event/raw/packet bytes, original database
timestamps, route proof and native clock files. DB0046 commits and reads back
the exact bundle in a different transaction. A final B1/companion readback and
actual native-clock check precede issuance. One actual monotonic timeout bounds
native acquisition and database awaits; it can only shorten to the original
source deadline. Late failure preserves accepted evidence and issues no owner.

`consume_owned_current_demo_account_v4` burns the owner first, checks its task,
loop, PID, thread, current credential lease and original expiry, and replays its
retained bundle. It returns private measured operands with earned current source
authenticity and `current_page_chains_complete=True`. All eight unfiltered algo
page chains are required. This names endpoint/page coverage, not an atomic
exchange-wide account snapshot. Known source clocks must satisfy the unchanged
strict causal checks. The account-position-risk `ts` is retained as an account
update anchor, not relabeled as HTTP generation time; balance `uTime` remains its
older last-change time. Missing, future or prebarrier required anchors deny.

`account_complete`, `post_g12_eligible`, portfolio/revision issuance, flat-start
permission and execution authority remain false. Full history, funding accrual,
daily/rolling loss, actual streak seed, historical HWM, local uncertain exposure,
active protection and final atomic risk recheck remain separate obligations.
The returned DTO and its boolean fields are not capabilities; a future assembler
must consume an opaque source owner in its own reviewed invocation.

`replay_current_account_bundle_v4` is private read-only audit replay. Even a
valid saved bundle returns `source_authenticity_verified=False` and cannot
reissue a current owner. DB0046 protects retention against its restricted
application role; it does not establish independent custody, hostile-DBA safety,
WORM storage or historical point-in-time availability.

The pinned lease label is not a credential. The existing packet includes it in
the copied plan and each collector-created page's metadata. Journal finalization
first verifies the exact packet/plan digest, then scans a canonical view omitting
only those exact validated metadata fields for label reflection. The original
bytes/hashes remain intact. Raw responses, decoded rows, arbitrary keys/fields,
API credentials and every signature retain their full secret checks.

Unit cases exercise synthetic transport/clock/database seams and do not prove
real OKX, native TLS or PostgreSQL acceptance. The actual account still requires
a successful signed read; a real 401/50110 is a denial, not source acceptance.
