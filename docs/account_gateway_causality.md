# V6 Demo account gateway-time coverage audit

`audit_recorded_demo_account_gateway_causality` is a read-only audit of an
externally pinned, original V6 current-only B1 journal. It replays the complete
13-stream packet and page chain through the existing current-source verifier,
then reports whether **every** original OKX response envelope supplied both
`inTime` and `outTime`. OKX defines these envelope fields as Unix microseconds.
The original parser checks `request_start <= inTime <= outTime <=
headers_received`; the audit does not replace a missing exchange time with the
host receipt or change the raw packet.

The versioned receipt lists each page's stream, index, raw-body and page-receipt
hashes, measured request/headers times, the original gateway timestamps when
present, and missing-page indices. It includes no raw account rows, UID,
credential, signature, or private header. One missing pair sets
`gateway_causal_coverage_complete=false` and adds
`exchange_gateway_time_missing`. A fully populated **synthetic** envelope can
demonstrate parser behavior, but does not authenticate an OKX response.

The bounded receipt constructor checks canonical bytes, exact schema and policy,
source digest shapes, all page indices and timestamp brackets, the missing-page
list, coverage flag, reasons, and fixed `DENY`/false/null fields. An internal
issuance fence prevents ordinary callers from constructing an audit object from
arbitrary receipt bytes. That Python object fence and any SHA-256 value are not
exchange authentication; a coherent fabricated packet still cannot be trusted
without the original owned source and session proof.

This audit has no native clock or credential-session owner and always returns
`snapshot=null`, `account_complete=false`,
`source_authenticity_verified=false`, `execution_authority=false`, and
`admission=DENY`. Its declared validation cutoff is not a current clock
capability. Exact registration region, complete historical cashflows,
exchange-wide account revision, local reservations/uncertain exposure, active
protection and a source-bound risk window remain separate requirements. The
existing V6 packet, proof policies and stored receipts are unchanged.

The negative and complete-envelope tests use in-memory synthetic B1 chains and
no private network access. They do not count as trusted Demo account acceptance.

Source: [OKX V5 API guide](https://www.okx.com/docs-v5/en/), which documents
the account endpoints and response-envelope timing fields.
