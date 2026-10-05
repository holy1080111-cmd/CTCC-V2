# Full-public V2 G1 diagnostic

This slice adds a versioned G1 result over an exact full-public V2 packet. It
does not establish candidate ownership, qualification completion, authenticated
account completeness, reservation, intent, execution recheck or order permission.
Every result keeps source_authenticity_verified, original_source_verified and
execution_authority exactly false, with admission DENY. A passing G1 record is a
reproducible diagnostic result, not an execution permit.

## Inputs and raw replay

evaluate_public_market_data_v2 accepts an exact CollectedPublicMarketV2, an
explicit expected bundle SHA256, DataQualificationPolicy and aware evaluation
cutoff. It does not accept PublicMarketContextV2, a domain MarketSnapshot, a
standalone quote, a V1 packet or a caller-produced PASS.

The bridge replays every raw quote, candle, books, open-interest and WS component,
including full packet canonical equality, fixed quote profile/transport pins and
the component schedule. It reconstructs a disposable market and independent WS
reference. Current freshness and closed candle tail coverage are checked at the
declared cutoff. Later mutation of a returned context cannot replace packet raw
bytes. Offline packet consistency does not attest the original TLS or IO owner.
The existing private runtime owner and its one-use same-invocation handoff remain
the separate acquisition boundary.

## One arithmetic core

data.py retains the V1 evaluate_data signature, CollectedQuote validation and V1
DataQualificationResult. Its original policy/source preparation and reference,
spread, mark, funding, OHLC quality and deterministic analysis checks are extracted
into private _prepare_market_data and _evaluate_data_tail helpers.

Both fixed wrappers use these helpers. The four Decimal quote operands are a
private arithmetic carrier with no source or authority claim. The V1 wrapper
validates the actual V1 quote before creating it. The V2 wrapper obtains the four
operands only after actual full-public replay and the fixed V2 profile inspection.
It does not fabricate a V1 ExecutableQuote or accept a caller validation callback.

The fixed V2 quote profile has distinct funding freshness semantics. Any stricter
G1 quote age still constrains ticker and mark source/header/body observations.
Exact Fraction comparisons determine mathematical boundaries before decimal
values are recorded. Quality and analysis are recomputed from the admitted rows;
caller quality, indicators and analysis are not used.

The source-only extraction review matched the original V1 check bodies and
ordering by AST. Exact V1 result-byte comparison and behavioral regression remain
required: extraction changes where private stage results are constructed, so
static body equality alone is not sufficient acceptance.

## Funding lineage

The G1 V2 record pairs the actual forecast rate with the upcoming fundingTime,
retaining raw body and canonical source hashes, exchange data-return time and body
completion time. nextFundingTime remains the following settlement forecast and
cannot substitute for the upcoming rate's settlement.

rate_generated_at and historical_first_available_at remain unknown (null).
Exchange data-return timestamps do not prove when a funding estimate was first
generated or historically available. This diagnostic cannot satisfy Point-in-Time
Gate 3 by relabelling those timestamps.

## Distinct result and strict readback

DataQualificationResultV2 uses schema ctcc.data_qualification.v2. It retains the
full-public, quote, profile and transport identities; inspection bytes/hash;
funding pair bytes/hash; G1 policy and reference identities; and rebuilt source
bytes/hash when the evaluated checks reach them. Passing records require complete
recomputed policy, reference and source pins.

evaluation_sha256 and verify_public_market_data_v2 share an exact record
preflight. It rejects foreign model types, hidden top-level or nested fields,
non-exact flags, foreign policy serializers and oversized character or UTF8 byte
strings before nested serialization. The verifier then evaluates the original
full raw packet again under the same policy and cutoff and compares complete
canonical result bytes. Self-signing a modified report, inspection, funding pair,
source or gate result does not replace that replay.

V1 and V2 result classes cannot be relabelled or accepted by each other's
verification boundary. No stored receipt renews freshness or grants permission.

The record preflight also checks exact bounded top-level scalar types and native
UTC timestamps before serialization. The nested gate must have exact fields,
bounded scalar types and 1 through 32 measured values. A measured container must
be an exact dict or a MappingProxyType whose sole native GC referent is an exact
dict. A proxy wrapping a foreign Mapping or dict subclass is rejected before
length checks, iteration or .items() can invoke caller code.

Measurements admit only exact bounded str, int, bool, None or finite Decimal
values. Foreign models, container values and Decimal subclasses are rejected
before their serializers or numeric methods run. Decimal raw representations
are bounded by 128 digits, absolute exponent 128 and rendered length 128.
Native equivalent dict/proxy containers remain admissible and produce the same
fingerprint; actual semantic verification still requires complete raw replay.

## Authored verification scope

tests/unit/test_public_market_data_v2.py uses the actual private full-public V2
owner over synthetic clock, TLS, IO and storage fixtures. Source validation and
G1 mathematics remain real. Cases cover full source/result pins, funding pair
semantics, six-second staleness, wrong packet versions/types, rehashed packet and
self-signed result tampering, hidden fields, hostile serializers, UTF8 bounds,
exact funding boundaries, hostile decimal contexts and distinct V1 result types.
The additional record guard fixtures cover direct and proxy-wrapped foreign
mappings, dict subclasses, foreign measurement/top/gate serializers, Decimal
subclasses, oversized decimal representations and custom timezone callbacks.
They assert no caller callback ran through either fingerprinting or verification.

These are authored synthetic tests. Their root-run results must be recorded
against the exact frozen source before marking this slice accepted. They do not
count as native market samples, Demo evidence, historical OOS or Live acceptance.
