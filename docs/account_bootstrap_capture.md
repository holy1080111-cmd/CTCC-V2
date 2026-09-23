# Cold-start Demo account capture

The normal account materializer still requires an initialized DB0017 account
revision. `ControlledDemoAccountSession.collect_bootstrap` provides the earlier
raw capture step without first inventing empty portfolio claims.

`QualificationLedgerRepository.initialize_capture_scope` takes the existing
environment/exact-UID advisory lock and inserts only the schema's explicit
revision-zero/null-claims scope when absent. It preserves any existing claims,
reservations, uncertain states and event tombstones. After commit it reads the
checkpoint in a separate session; uncertainty, changed state or a readback that
starts before the prior observation completed rejects the call.
The new `LedgerBootstrapCheckpoint` type is not accepted by the ordinary account
materializer. Its empty local hold list does not describe exchange exposure.
`scope_active_hold_count` counts only this settlement scope; it is not an
account-wide flat-state assertion. The new scope/checkpoint junction rejects
foreign Python objects, dirty model fields and untrusted timezone callbacks before
invoking shared replay helpers, including values nested inside retained holds.

The one-use controlled credential session performs the same signed regional Demo
GET chain, retains the raw packet, verifies packet/plan replay and exact UID,
and requires identical local state before/after capture. Known active local holds
remain observable for reconciliation reads and are never released. Missing scope,
invalid revision/null pairing, changed state, malformed or incomplete packets,
wrong identity, uncertain commit, cancellation or reversed clocks cannot produce
a successful capture receipt. A failed session is never retried.

The separately versioned `ctcc.demo_account_bootstrap_capture.v1` receipt omits
account identifiers and credential material; the packet itself remains private.
Native signed verified TLS and synthetic transport retain distinct classifications.
Neither classification proves historical completeness or registration provenance.
All bootstrap results retain `account_complete=false`, `admission=DENY` and
`execution_authority=false`; they publish no account claims revision and do not
Arm, reserve, submit, resolve exposure or relax the ordinary materializer.

The synthetic runtime/account compatibility set passes 219 cases, including 22
bootstrap boundary probes. Independent review first reproduced a cross-session
clock gap and three foreign-scalar callbacks; the fixes preserve both ordinary
v3/v4 runtime receipt byte sequences from frozen source `037fca4`.
The wider 266-case command recorded 251 passes and 15 setup errors because the
existing system temporary directory was inaccessible. Rerunning the affected
migration identity module with a fresh workspace temporary directory passed all
47 cases. The original failed run is retained; no test or publisher permission
check was removed.

These results do not validate the new repository's PostgreSQL behavior: four
real PostgreSQL integration cases are present but remain unrun while the isolated
database is inaccessible. No authenticated account request has been made for this
change. Trusted registration/history/peak/accrual/product verifiers and the
genuine account-revision issuer remain required.
