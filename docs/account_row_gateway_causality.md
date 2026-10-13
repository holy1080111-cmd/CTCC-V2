# V7 source-row versus REST gateway chronology

`account_row_gateway_causality.audit_v7_account_row_gateway_causality` replays
the externally pinned original V7 B1 account page chain and the existing V7
gateway audit. Its separate `ctcc.demo_account_row_gateway_causality.v1`
policy does not change older packet, current-source or gateway audit policies.
It compares every retained, non-null row source timestamp with that page's
original REST `outTime`. A source time after `outTime` is a blocking
`source_timestamp_after_gateway_response` contradiction. No tolerance or
timestamp substitution is applied. A missing `outTime` remains
`gateway_missing`/`exchange_gateway_time_missing`; a missing row timestamp is
counted, never filled with zero or receipt time.

[OKX's official API guide](https://www.okx.com/docs-v5/en/) defines REST
`inTime` as the gateway's request-receipt timestamp and `outTime` as the
gateway's **response-send** timestamp, both in Unix microseconds. These
exchange timestamps are distinct from the application's measured HTTP headers
receipt, body completion, and response close. A historical fill or bill source
time may be before this request. The check only rejects one that is later than
the response's own send time. It does not claim all source rows were available
at request start or establish an exchange-wide account snapshot.

The receipt contains page hashes, bounded counts, conflict page indices, and
fixed `snapshot=null`, `account_complete=false`,
`source_authenticity_verified=false`, `execution_authority=false`,
`admission=DENY`. It excludes raw account rows, UID, credentials, private
headers and source timestamp values. Independent readback requires an external
receipt SHA-256 and replays the original chain. A no-conflict result remains
diagnostic only: registration origin, native credential ownership, complete
history, local uncertainty, account revision, risk window and protection are
still unproved. No Demo/Live order route consumes this receipt.

The focused synthetic regression covers a row time after gateway send but
before host receipt, missing gateway time, no contradiction, fixed DENY fields,
external readback pin, and rejection of caller-minted receipts. It does not
constitute authenticated Demo account acceptance.

The separate [V7 current-use revocation](account_current_use_revocation.md)
replays this audit and retains its receipt hash. It also prevents a new V7
native flat precursor from being issued. Existing V7 source/native proof bytes
remain historical replay evidence; their earlier `observed_flat` diagnostic is
not current-use flat qualification.

The separate `ctcc.demo_native_current_v7_chronology_proof.v1` offline contract
can now replay an independently pinned historical V7 native-clock proof and
its complete original B1 page chain. It requires every page's original OKX
`inTime`/`outTime` pair, all pairs within the measured request/header window,
and every *recognized* source timestamp present and no later than the **same
page's** `outTime`. A row such as account config may have no recognized source
clock at all. The receipt counts those rows separately and fixes
`source_time_coverage_complete=false`; it never treats zero clock fields as
proof of availability or account completeness. A missing value for a recognized
timestamp prevents even this limited chronology proof. The receipt binds the
historical native proof, source reference,
gateway and row-audit receipts, and ordered per-page raw/receipt and per-row
source hashes. A second call with an externally pinned receipt SHA replays all
of those inputs. The stored-companion entry point first independently reopens
the historical no-clobber native proof/readback and then checks these exact
bytes again; its synthetic test also rejects a mismatched stored readback pin.
Missing/malformed gateway time, a future row time, changed
source, stale original proof, or mismatched pin denies. The proof contains no
raw private rows or credential data; all authority and flat-start fields stay
false. This is an offline diagnostic over a pre-existing proof, not a new
native runtime issuer or a newly durable chronology receipt. The V7 native runtime
issuance revocation remains in force; an owned, separately durable current-use
issuer/readback and complete account/portfolio evidence are still required
before any Demo qualification.
