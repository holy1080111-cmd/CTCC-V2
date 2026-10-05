# Account native-clock proof: required next-version contract

This is the original native acquisition/storage design contract. The initial
current-source implementation now under source review is described in
[Initial native account source](account_initial_native_source.md). Genuine native
acceptance for that new entry remains unexecuted. The current B5 diagnostic
V2 deliberately has no issuer implementing it. No authentic account capture,
native-clock proof, HWM-clock acceptance or current owner is claimed here.

## Why B1 V1 cannot issue current ownership

B1's successful private TLS transport proves the source transport. Its UTC
request, header, body and journal observations come from a trusted injected
callback. Native monotonic time bounds actual network acquisition, but the old
journal contains no original OS/monotonic sample per observation and no admitted
host-clock proof bracketing that acquisition. A random invocation owner digest
and source hash do not prove that clock or bind the later consuming task.

Do not retrofit a proof by taking OS samples later, replacing raw receipt times,
renaming a callback, accepting a caller native handle, or interpreting all TLS
flags as native time. An old capture with no original companion stays unknown.

## Required native entry

A future controlled account coordinator must issue one private invocation before
source I/O. It owns the exact controlled session, pinned scope/plan, publication
or initial-stage boundary, parent task, event loop, PID and thread. Its public
entry accepts neither a UTC callback nor a datetime or native-proof DTO claiming
to be a publication barrier. The actual private publication/initial-stage issuer
supplies that boundary, preserving strict after-barrier request ordering.

The coordinator uses the existing reviewed native OS sampler, including the
Windows precise FILETIME/QPC domain. It admits the existing reviewed host-clock
health plus direct OKX public-time probes before and after acquisition. Their
original raw bytes, version/profile, strict causal brackets and hashes stay bound
to this invocation; old host-time evidence is not silently reused. A failed,
unknown, expired or regressed clock admission denies the account owner.

The source transport must emit an original native UTC/monotonic witness for each
actual request start, headers receipt, exhausted body completion and account
source closure. Every witness has an explicit phase and source ordinal emitted
at that phase by the controlled collector, not inferred later from arbitrary
callback call counts. If V1 cannot emit those witnesses, a reviewed versioned
native collector adapter/hook is required; this slice does not alter B1.

## Proposed immutable companion schema

The source root schema is `ctcc.demo_account_native_clock_proof.v2`. Its exact
canonical encoding and policy hash still require preregistration before
acceptance. At
minimum it retains:

| Field | Requirement |
| --- | --- |
| Scope | Exact environment/UID/currency/session binding in private storage; public evidence exposes only necessary digests. |
| Source | Exact original capture ID, plan SHA, packet SHA, journal terminal sequence/head, invocation owner SHA and local checkpoint SHA. |
| Boundary | Exact private stage identity and original native publication/initial-stage stamp, both UTC and monotonic, plus original expiry. |
| Host admission before/after | Original clock-health/probe artifact hashes, admitted binary/resource profile, receipt hashes and actual observation stamps. |
| Clock domain | Reviewed wall and monotonic implementations/units/resolution/profile, with no caller-selected override. |
| Phase witnesses | Ordered sequence, phase, request/page ordinal, actual native UTC/monotonic stamp, unchanged B1 UTC value, raw/page/row locator as applicable and previous witness hash. |
| Source joins | Exact safe request identity, retained raw/body/page receipt hashes, TLS peer binding and matching original journal event/packet identities; private signed headers are excluded. |
| Closure | Original native source-close, proof-persist, commit/readback and consume-boundary stamps; incomplete closure never becomes success. |
| Membership | Complete requested source-phase inventory, original phase-witness hashes, terminal digest and exact proof policy identity. |
| Readback | Separate-session or immutable no-clobber readback of the exact complete companion and source binding; missing/changed bytes deny. |

Native source-phase witnesses must agree with the unchanged B1 UTC fields under
the explicitly reviewed conversion precision, while their monotonic sequence
and wall/monotonic deltas obey the existing strict clock policy. Do not increase
tolerance, use a later stamp for a missed phase, or change an exchange timestamp.

The companion belongs to the original account evidence/storage lineage. It is
not a new risk reservation or financial ledger. A reviewed additive storage
contract must prevent modification/deletion/rebinding, late witness insertion
and acceptance detached from the existing journal terminal. The source may be
committed before its proof: a crash then leaves an unaccepted source with absent
proof, never a retroactively accepted owner. Companion commit uncertainty denies;
reconciliation/readback never resumes source I/O or remints current ownership.

## HWM history remains separate

Every accepted HWM sample through the same immutable sequence-1 genesis must
have its own original admitted companion, exact source binding and native
balance-phase witness before `native_sampled_hwm_verified` can be true. A current
native proof does not attest older samples. Existing V1 captures without those
original witnesses remain explicitly unknown. No history reset, prefix omission,
new genesis or later proof invented for an old measurement is allowed to repair
that gap. A different risk window remains unmatched.

## One-use boundary

Only an actual successful native issuer after durable proof/source readback may
populate the private boundary registry. `account_clock_boundary.py` itself
contains no registration API and rejects a missing registry entry. The separate
initial native runtime's new current-only issuer is under review; it issues no
B5 component owner. The boundary's defensive
consumer requires an exact private identity and registered proof policy/digest,
not a caller attestation. A future issuer must additionally verify every source
join described above before registering that digest.

The component owner binds that boundary and its exact receipt digest. Consumption
burns the owner first and burns the boundary on all outcomes. It requires the
same private invocation, original parent task/loop/PID/thread and an uncanceled
parent. It independently reads the reviewed native OS clock and checks both UTC
expiry and the original monotonic deadline. A frozen wall-clock value cannot
hide elapsed monotonic expiry; a clock jump or regression denies. Maximum token
lifetime is 30 seconds and may only be shortened by the original source/barrier
deadline. A consumed historical receipt remains audit evidence forever.

## Required validations before adding an issuer

The next version needs source-phase completeness/join adversaries, wrong clock
domain or host-profile rejection, callback/native-handle rejection, strict
barrier chronology, independently expired monotonic time, missing HWM companions,
changed original witness, late witness insertion and no-clobber readback cases.
Storage tests must cover fresh schema, constraints, concurrent publication,
rollback, uncertain commit, process loss between source/proof commits and restart
readback. Genuine native account acceptance remains separate from fixtures.

Until this contract is implemented and validated, the current entry remains
diagnostic, `owner=None`, `snapshot=None`, `admission=DENY` and no execution
authority. Existing V1 artifacts remain immutable historical records.
