# Controlled Demo account runtime — incomplete admission

The current runtime receipt is separately versioned as
`ctcc.controlled_demo_account_runtime.v2`. It binds a
[captured instrument metadata receipt](captured_account_metadata.md): instrument
rows now come directly from this invocation's replayed source packet. Caller
instrument DTOs are optional exact assertions and cannot replace missing or
conflicting source rows. Historical account packet v2/v3/v4 bytes are unchanged.

The owned session also accepts the exact `AllProductDemoAccountCapturePlan` v4
contract. It acquires 38 mandatory standard-product/current-inventory streams,
then replays and maps every typed history row. See
[v4 coverage and remaining gaps](qualification_account_v4.md). A v2/v3 packet
cannot acquire v4 coverage by changing a version, scope, or receipt hash.

`ControlledDemoAccountSession` owns a copied credential handle and a pinned
`RegionalDemoAccountCapturePlan`. It connects the existing 23-stream collector to
the existing account materializer and DB0017 qualification journal. It does not
load credentials, create orders, arm, advance account claims, release holds, or
issue a submission permit. A complete synthetic acquisition can finish mapping
while admission remains `DENY`; this is not Demo acceptance.

## Acquisition and revision binding

The new regional plan produces `ctcc.demo_account_capture.v3`. Historical v2
plans/packets retain their fields, default `www.okx.com` origin and exact hash
calculation. A regional plan requires an explicit registration region, matching
origin and registration-evidence hash. Reviewed official origins are global
`openapi.okx.com`, US/AU `us.okx.com`, and EEA `eea.okx.com`.
Registration is never inferred from locale, redirects, a configured URL, or a
successful HTTP response. The supplied registration artifact hash is still a
claim until independently authenticated.

The private collector owns signing and a fresh isolated HTTP client. GET only,
`x-simulated-trading: 1`, no proxy/environment inheritance, retry, redirect or
source substitution. Every request starts after the supplied publication barrier.
Native responses must expose a negotiated TLS 1.2/1.3 session under the exact
verified client context, expected server hostname and nonempty peer certificate.
The process-local result records peer certificate hashes. MockTransport is always
classified `synthetic_transport`; serialized packet replay cannot create this
owned acquisition result. This local provenance is not a portable cryptographic
attestation, exchange account completeness or evidence of key permissions.

`QualificationLedgerRepository.read_capture_checkpoint` reads persisted claims,
their hash/scope, revisions and active holds under the existing account row lock.
Runtime reads checkpoints before and after capture. Both state hashes must match;
changed revision/claims/holds, missing revision, clock reversal and any unresolved
hold deny the attempt. Runtime constructs `LedgerEvidence` from the first actual
DB observation after verifying the second; caller-supplied ledger evidence is
rejected. Neither read changes durable state. Matching local revisions do not
claim a shared exchange revision across sequential endpoint reads.

## Immutable bootstrap requirements

`AccountBootstrapEvidence` and `BootstrapArtifact` retain externally pinned raw
bytes, SHA256, source-receipt hash, exact Demo UID/mainUid/currency/registration,
instrument coverage, measured receipt and coverage interval. Canonical freeze and
readback reject duplicates, malformed/oversize records, changed bytes, mismatched
scope, future seals and insufficient declared history interval. Runtime checks
registration bytes against the plan pin and names every missing artifact kind:

| Artifact | Required source verifier / producer still missing |
| --- | --- |
| registration | Authenticate the account registration/region artifact through an owned setup session. |
| history seed | Derive the required initial loss streak from verified inception, an immutable opening state, or a sourced strictly positive net reset and complete subsequent outcome chain. Preserve every outcome needed for daily/7d loss; a reset inside that window cannot be inserted as a zero seed at its earlier start. |
| history retention | Verify endpoint-specific retained intervals against requested cutoffs and durable archives. Empty terminal pages do not prove older history absent. |
| history ingestion watermark | Ingest all required products/pages, detect gaps/conflicts/late arrivals and bind closed immutable intervals. |
| continuous peak window | The old continuous-path claim remains unverified by finite samples. A new explicit measured-HWM/DD-window policy may be implemented without claiming an unsampled maximum; preserve old bytes and the accepted window/peak across restart. |
| funding accrual | Link independent instrument accrual events and held-position intervals to bill identities; bill-generation `ts` is not accrual time. |
| account product scope | Collect and verify all applicable products, liabilities and advanced-account inventory, including unsupported-product denial. |
| instrument coverage | Verify metadata/cost/correlation and leverage coverage for every actual instrument, not only a caller-selected list. |

The current bootstrap reader validates integrity and declared scope, not these
source claims. Supplying all eight artifacts cannot clear the materializer's
history/peak/product/authenticity gaps. These missing verifiers are engineering
work, not merely absent user credentials. The next integration must consume their
owned verified outputs before changing runtime admission. Preserve existing DTO
`account_complete=False` and all authority flags; do not turn a stored manifest,
caller boolean, empty current inventory or sampled peak into account authority.

[B2a observed history queries](account_history_query_verifier.md) now replay the
finalized B1 raw journal in a separate session and derive bounded Global
generation-time fills/bills query coverage. That source verifier does not clear
the older bootstrap claims or issue account completeness. A later trusted local
account revision can bind verified observations under a defined coherence policy;
it need not claim a shared atomic exchange revision.

The current runtime also conservatively denies any active reservation/intent;
future non-flat operation needs exact persisted order/protection lineage and a
verified union of current exposure and local holds. A separate transaction must
then generate the authenticated account revision used by R6/R7; this module never
calls the older caller-claims `reconcile_scope` as a shortcut.

## Validation

Synthetic tests exercise the complete owned legacy 23-stream and v4 38-stream flow for explicit
region, exact UID mismatch, Live rejection, changed DB revision/claim hash,
unresolved holds, clock reversal, foreign/replayed result, missing native TLS peer
evidence, bootstrap tampering/scope/coverage, missing history, and secret redaction.
Existing capture/collector/materializer/intent regression remains required.
PostgreSQL tests exercise checkpoint reads, preserved holds, bad persisted claims,
and clock failures with actual row locks; they require a dedicated isolated DB.

```powershell
python -m scripts.hermetic_pytest -q tests/unit/test_qualification_account_runtime.py
python -m scripts.hermetic_pytest -q tests/integration/test_qualification_ledger_repository.py -k capture_checkpoint
```

No authenticated OKX account was read and no Demo or Live order was submitted for
these tests. No full trusted-account or final submit-route PASS is claimed.

Official contracts reviewed 2026-09-19: [overview and registration routing](https://www.okx.com/docs-v5/en/#overview),
[Demo services](https://www.okx.com/docs-v5/en/#overview-demo-trading-services),
[pagination](https://www.okx.com/docs-v5/trick_en/#pagination).
