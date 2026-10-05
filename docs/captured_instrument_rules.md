# Exact captured instrument rules

`derive_captured_instrument_rules` replays a complete externally pinned account
packet and extracts one exact `account_instruments` row. The additive helper
does not change `ContractRiskSpec`, legacy metadata mapping, materializer or
their serialized bytes. There is no caller specification, default tick/lot,
rounding, sizing, candidate selection, order request or authority input.

The reviewed policy is Demo linear `BASE-USDT-SWAP`, settlement USDT, state live,
captured `ctValCcy` equal to that exact base, and explicitly captured `ctMult=1`.
Absent `ctMult` is unknown and rejects; it is never filled with one. Inverse,
other currency/product, conflicting optional base/quote fields and missing or
duplicate source identities reject. The complete raw packet is independently
replayed before extraction; hashes do not replace its original row/body proof.

`tickSz`, `lotSz`, `minSz`, `ctVal` and `ctMult` require actual string operands,
strict positive plain decimal syntax, at most 20 integer and 20 fractional
digits, and no exponent, float, bool, leading whitespace, NaN or infinity.
Values are exact Decimal/Fraction representations. Trailing source zeroes are
retained in the receipt. `minSz` is an independent lower bound and is not forced
onto the lot grid. A future quantity policy must satisfy both that bound and
the unchanged lot grid. This helper never rounds an off-grid price or quantity
into acceptance.

The rules retain their units: tick size is USDT per base currency; lot and
minimum size are contracts; contract value is base currency per contract.
The result binds exact plan/packet, Demo UID/mainUID/session lineage, original
request and row indices, row hash, page receipt, raw response body hash and
canonical page hash. It retains measured request/header/body chronology.

The instrument endpoint does not establish an exchange as-of timestamp in this
reviewed contract. Missing `ts` remains explicitly absent. An optional returned
`ts` retains its raw value and unknown endpoint semantics; it never becomes an
exchange-as-of claim or receives the measured body-completion timestamp.
The helper itself makes no freshness or historical-contract-validity assertion.

Results are private diagnostic evidence. `source_authenticity_verified`,
`current_owned_session_authority` and `execution_authority` remain false.
A future original-candidate owner must invoke the helper on the actual packet
retained inside its own credential/account/session acquisition and keep that
ownership through use. A copied or rehashed result cannot recreate ownership.

Tests are authored for exact bindings/units, optional timestamp semantics,
missing/malformed fields, duplicate row/page, unreviewed contracts, wrong pins,
caller-result substitution, independent min/lot bounds and decimal-context
independence. Execution is deferred to the coordinator's bounded schedule after
host thermal stability is established; authored tests are not acceptance results.
