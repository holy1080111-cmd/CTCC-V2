# V4 current Demo market source

`acquire_initial_demo_market_source_v4` is the initial, account-first full source
entry. It requires an unused controlled Demo account session, its matching native
private client, a reviewed instrument, the existing bounded full-market policy,
distinct empty controlled public-journal and private-account-proof roots, and a
separately restricted PostgreSQL runtime-custody repository. It accepts no source packet, clock, route, report ID,
account observation, caller source-auth flag, HTTP client or saved receipt.

Before exchange I/O, the custody repository verifies the actual native database
login, role restrictions, catalog definitions, enabled append-only guards and
durability settings. Missing migration, missing procedure grants, an administrative
or table-owning connection, direct DML access, changed definitions, or disabled
guards fail with `public_v4_custody_deployment_unavailable`. Configuration alone
does not issue a custody or source capability.

The source then performs one native signed Demo account/config GET. Exact UID,
main UID, observed account/position modes, credential contents, active controlled
session, reviewed origin, TLS peer and measured clocks stay bound to the issuing
invocation. Its initial lease is consumed within five seconds and moved into the
existing sole public runtime owner. The accepted config bytes/proof are published
with no-clobber readback in the private proof root before public acquisition, so
a subsequent market/custody failure retains that account evidence. Every later transport check revalidates that
live binding. An accepted signed response establishes an operational route for
that key and exact account at that time. `registration_site=UNKNOWN` and the old
`registration_region_authenticated=false` remain accurate.

The fixed full schedule is exchange time before, all four candle frames,
concurrent quote/books/open-interest/WebSocket, then exchange time after. Quote
collection remains funding, mark, ticker. The native owner retains every raw
HTTP body and exact WS application message, request/query/header identity,
headers/body/close/validation measurements, TLS binding and native clock evidence.
There are no redirects, environment proxies, source substitutions, hidden retry,
missing-value repair, or quote reacquisition. Missing, duplicate, malformed,
future, expired or incomplete required data rejects the attempt. Strict V4 plan
replay and full raw inventory joins complete before custody.

The native owner's summary digest is checked against `summary.json` before bundle
freeze and again after exact database readback. Re-signing a journal while keeping
its numeric market packet unchanged cannot replace its original TLS/clock evidence.

The source-owned config probe also keeps a bounded private attempt plan and terminal
receipt on rejection, malformed body, timeout, cancellation and late cleanup failure.
It retains up to 8192 observed raw bytes with measured status/stamps and a digest of
all observed bytes. Truncation, absent completion stamps and missing raw are explicit.
Credential, HMAC signature or private-header echoes are never written as raw; only
safe metadata and digests survive, with `raw_retained=false`. Attempt storage failure
burns any issued lease and never deletes already published evidence. These files
remain in the private account-proof root, outside public journals and report payloads.

The V4 raw bundle preserves all journal files byte-for-byte plus the private
account/config proof. Account identifiers remain necessary private evidence;
public status receipts expose hashes. Authentication headers and signatures are
not stored. DB0046 retains the complete raw bundle with its SHA256 and exact
operational-proof digest through restricted append/read functions. It commits
once and reopens another session for exact bytes and deployment readback. An
uncertain commit creates no source handle and never triggers another append or
exchange request. Already published local evidence remains available on failure.

This custody protects retained bytes against the restricted application role.
It does not claim independently administered WORM storage, malicious-DBA safety,
or historical point-in-time availability. It is a separate current-observation
contract; historical V2/V3 trust requirements and their native DENY behavior are
unchanged. Gate3 availability provenance remains independently required.

After successful native acquisition and committed raw readback, one opaque
`OwnedCurrentMarketSourceV4` can be consumed by
`evaluate_owned_current_market_v4` in its issuing task/process/thread/loop. The
consumer replays retained bytes, reconstructs a real `MarketSnapshot`, checks
freshness again at the actual post-custody clock, and evaluates/verifies shared
G1 mathematics. The existing fixed G1 engineering inspection policy remains
explicitly uncalibrated for trading. Source authenticity and durable raw readback
can be true; G1 may still fail its mathematics. No DTO, saved bundle, rehashed
receipt, caller boolean or offline replay can recreate the opaque source handle.
Wrong-task, expired, cancelled or second consumption burns or rejects it.

The nested V2 numeric packet retains its historical false flags and exact bytes.
SWAP candle `vol`, `volCcy`, `volCcyQuote` remain contracts, base and quote currency.
Ticker `vol24h` and `volCcy24h` remain contracts and base currency; missing quote
turnover stays unknown. `oiUsd` remains separate from contracts and currency.
Funding economics retain the rate/upcoming `fundingTime` pair; the shared snapshot
still preserves the following `nextFundingTime` forecast separately.

This implementation does not complete portfolio/account history, G2–G12, the
post-G12 V4 owner, risk reservation, durable intent or any submit route. Those
authorities remain separate. No order writer is imported or invoked. The public
result explicitly keeps `account_complete=false`,
`historical_availability_verified=false`, `execution_authority=false` and
`registration_site=UNKNOWN`.

Focused tests use visibly synthetic TLS/clock/account/database seams to exercise
the complete positive control flow, raw proof/inventory tampering, absent custody,
uncertain commit, one-use task ownership and old V2/V3 denial. They are not native
OKX or PostgreSQL acceptance evidence. Real source acceptance requires an actual
configured DB0046 restricted deployment and a new authenticated native run; a
prior source commit or CI run cannot establish that result.
