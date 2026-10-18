# V7 signed-9549 native chronology diagnostic

This is a separate, **DENY-only** initial Demo account diagnostic for the
existing V7 current-account plan. The former V7 flat issuer remains revoked
before the first account GET in `current_use_contract_for_plan`; old V7 proofs
and receipts retain their historical replay policy and hashes. The new
`capture_native_v7_9549_chronology_diagnostic` entry point does not call that
flat issuer and cannot issue a current carrier, account-clock boundary, origin
lease, raw-packet lease, account revision, snapshot, reservation or order
permission.

The one-use controlled session owns the initial native stage. Its original
`host-before.json` must be published and read back before the first public
time or private account GET. The selected policy is pinned from that accepted
original host observation: exactly signed Windows `w32tm.exe`
`10.0.26100.9549`. A missing, rejected, old-9278, unrecognized or mixed
before/after profile fails. A caller's plan, proof document, environment
variable, or profile label cannot select or repair the profile.

The existing B1 journal and V7 source verifier replay the exact original
current packet, including the eight separately queried algo types, raw/page
hashes, cursor chains and explicit terminal pages. The new native proof keeps
the original phase, public-time and 30-second freshness checks, then requires
each original REST gateway `inTime`/`outTime` pair and each recognized row
time to be causally ordered relative to its own page. Missing recognized row
time, missing gateway time, page/hash mismatch, stale source or extra
current-source blocker rejects the proof. Rows without a recognized source
clock remain counted as unknown; no missing timestamp is synthesized.

The proof is published without clobbering and reread from a separate storage
handle. The original account journal is reread again under the exact-UID
lock. The returned versioned receipt contains hashes, page and row counts,
the selected 9549 profile, the native readback hash and causal audit hashes,
but no raw account body or credential. Both a complete recorded empty
inventory and a complete recorded nonempty inventory can yield this
diagnostic. Neither becomes current flat qualification. Every result has
`admission=DENY`, `current_flat_qualified=false`,
`account_complete=false`, `flat_start_permission=false`,
`source_authenticity_verified=false`, `execution_authority=false` and
`snapshot=null`.

The source contract follows the existing V7 policy. In the current official
[OKX API guide](https://my.okx.com/docs-v5/en/), the pending algo list uses
`GET /api/v5/trade/orders-algo-pending`, an `ordType` query, `algoId`
pagination via `after`, and a maximum `limit` of 100. The guide also
documents REST gateway `inTime`/`outTime` in Unix microseconds and
`x-simulated-trading: 1` for Demo requests. The relevant registration site
for this account remains unproven, so this source review does not certify
the live endpoint or the account's actual query inventory.

Only synthetic fixture tests currently exercise this contract. A memory
scenario drives the actual one-use native stage and B1 collector through
both empty and nonempty V7 diagnostic paths, including separate storage and
journal readback; it explicitly relabels MockTransport TLS metadata after
capture to test control flow and **does not prove real transport ownership**.
Other tests cover the full eight-algo replay, wrong/mixed/missing clock
profiles, wrong policy hash, missing gateway time, future row time, and
pre-GET rejection. There has been no authenticated OKX read, native 9549
field capture, production PostgreSQL run, source-region proof, complete
portfolio reconciliation or execution acceptance for this slice. It cannot
satisfy the trusted-account or Demo gate.
