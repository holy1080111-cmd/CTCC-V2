# Public receipt publication ACK (0033 engineering prerequisite)

**Status: PostgreSQL migration/repository and offline tests implemented;
restricted-role PostgreSQL acceptance and deployment custody remain unverified.**
This is not a Gate 3 measured-availability proof, an OOS seal, or trade
authority. No capture scheduler, MIE predictor, Demo, or Live route uses it.

The existing v1 public minute receipts, journal entry bytes, hashes, and
`measured_public_minutes` are unchanged. Their `available_at` remains the
earlier capture-validation time and must not be promoted into a predictive
decision-time claim.

Migration `0033` adds a one-per-capture-sequence append-only PostgreSQL ACK.
Its server guard reads a committed `0024` `append_capture` witness revision
under `FOR KEY SHARE NOWAIT`, checks the exact witness/checkpoint hashes and
state, derives the capture sequence and journal head from the witness row, and
samples server time. The caller cannot supply the ACK time, capture sequence,
or journal head. The ACK login must have only the narrowly granted ACK append
and read functions. It cannot write or read the witness, own either table,
inherit another role, create schema/database objects, or use direct table
privileges. The trigger checks `session_user`, so granting witness append to
that login makes ACK insertion fail even if it writes a witness in the same
transaction. No role or grant is created by migration. The database and OS
custody boundary still requires deployment evidence.

An ACK's `acknowledged_at` is **before the ACK transaction commits**. It proves
that the already committed witness was visible to the independent login at the
server observation instant. It does **not** prove that a historical decision
could read the ACK then. The repository commits, then reads the ACK and complete
witness chain in new sessions and checks database timestamps against host UTC
and monotonic brackets. That later readback timestamp is currently returned
only in memory, so restart cannot replay its historical timing. Both the ACK
time and readback time are excluded from predictive availability. A future
version must bind a post-ACK-commit observation durably, verify its clock and
role custody, and replay exact journal bytes to the witnessed capture head
before any row becomes eligible for Gate 3.

The result fixes `trusted_clock_verified`,
`historical_decision_availability_verified`, `independently_protected`,
`predictive_oos_eligible`, and `execution_authority` to false. Unknown commit,
missing/changed witness, role drift, timestamp inversion, clock-bracket drift,
duplicate ACK, or failed readback raises a safe error. It never retries a
possibly committed write or turns a missing record into an empty result.

The focused offline tests cover DDL/model alignment, role-grant shape, clock
rejection, Linux/Windows suite classification, and the fixed authority flags.
The disposable PostgreSQL tests cover restricted login, an uncommitted witness,
same-transaction witness grant, exact witness identity, separate-session
readback, immutable ACK, and downgrade/re-upgrade. Those PostgreSQL cases have
not passed locally on this host because no isolated `DATABASE_URL` is
available. The final source must run them under its exact PostgreSQL CI and
restricted-role deployment before treating even this narrow prerequisite as
accepted.
