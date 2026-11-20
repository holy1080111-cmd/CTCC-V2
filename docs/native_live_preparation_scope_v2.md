# Native Live preparation scope: phase 1

The preparation scope is DATA liveness, not execution eligibility. It retains
the original registry, task/loop/PID/thread/ContextVar, held session and exact
transaction, consumed/spent/not-revoked qualification, current runtime/settings,
credential lease/source configuration, account/source revision, public-owner
lineage, original intent/request/readback bytes, and current control checks.
It does not cache a mathematical PASS or extend a deadline.

Preparatory transport constructors, host/TLS-context getters and ownership
binding use this scope. The full current proof remains inside every `ssl_write`
and native `raw_write`, including TLS handshake writes. An outer call no longer
performs the same full replay immediately before the method repeats it.
The actual post-TLS held readback and full proof after its last database await
remain mandatory. The close-gate family retains its existing full behavior.

The existing API integrity cache now has an exact, fixed-identity one-slot list.
Only its validated API binding data populates. The coordinator's genuine source
guard therefore pins both whole physical and transport namespaces without a
mutable-global exemption. Changed cells, malformed/foreign cache contents,
changed helpers and in-place method/accessor changes are negative controls.

`test_native_live_preparation_scope_v2.py`, the unchanged physical unit suite and
the genuine Global source-closure suite pass 108 tests, zero skips, in
`N319/N388_PREPARATION_FINAL.xml`. These include synthetic DATA-only source/SQL
admission and an explicit test that preparation cannot authorize actual write
methods. They are not configured-runtime or venue acceptance.

## Phase 1 native lower bound remains FAIL

`N319/N388_PHASE1_FAITHFUL443.xml` uses the same native-paced raw worker and real
vendor/TLS/socket path to a temporary private-CA loopback peer at port 443.
Runtime/account/credential/SQL admission and the source guard remain explicitly
synthetic. No real key, operator Arm, database or venue is used.

TLS now completes. The unobserved timing case sends one complete synthetic
FOK POST, then expires before its response is validated. The observed case
sends the headers; the unchanged cash cutoff denies the body native write.
Both remain UNKNOWN with risk held. The stale-instrument negative passes.
This is two failing acceptance cases and one passing refusal, not a full PASS.

Observed time after the new mutable quote includes TCP at 4.005 s, TLS at
4.012 s, header SSL/native callbacks at 4.396/4.485 s, and body callbacks at
4.661/4.748 s. The source-owned counter observes 93 inert q guard calls after
intent, 14 `_qualified` and eight full physical checks. No multiplier is
inferred from another counter. Real source-guard/SQL/account/network cost is
excluded, so these remain optimistic bounds.

The duplicate saved-guard invocation and canonical-session guard are deliberately
retained in this first phase measurement. Response-only DATA retention is also
unchanged here. Subsequent changes need independent source-integrity/no-callback
tests and the same native write/expiry/EStop controls; they cannot infer readiness
from this checkpoint.

## Identity-only follow-up

The shared continuation checker now factors an inert callable-metadata helper.
The existing checker still validates metadata and executes the guard. Preparation
uses the identity-only check immediately before the original canonical session,
which executes the actual source guard itself. Fixed session/task/error/guard
function, global and builtin bindings are checked before delegation, so a
foreign prefix callback cannot run first. These are source-integrity pins, not
eligibility results. No runtime replay, clock or actual write guard is removed.

The new helper/prefix tests plus unchanged continuation, physical and source
closure suites pass 142 tests, zero skips (`N388_IDENTITY_PREFIX_FOCUSED.xml`).
The corresponding faithful loopback run has two passing cases and one failing
observed ACK case (`N388_IDENTITY_PREFIX_FAITHFUL443.xml`). The unobserved case
delivers one complete synthetic request, validates its reply and reads back the
synthetic disarm successor. The observed case also delivers the complete request,
but the existing execution expiry gate prevents late response validation.
Its first native application enqueue is 4.412 seconds after the mutable quote;
71 inert source-guard calls occur after intent, 17 after that enqueue.
This is still a lower bound, not configured-runtime or whole-route acceptance.
