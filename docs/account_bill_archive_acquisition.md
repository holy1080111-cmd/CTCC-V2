# Quarterly bills archive diagnostic contract

`app/trade_qualification/account_bill_archive_acquisition.py` models one Demo
account/quarter/all-bill-types acquisition attempt. It contains no HTTP client,
signer, credentials, scheduler, downloader, account snapshot, or order path. Every
journal state has `admission=DENY`, `account_complete=false`, and
`execution_authority=false`. Caller-supplied responses remain unowned diagnostic
bytes; their hashes and replayed state do not establish OKX authenticity.

The immutable plan pins the exact UID and main UID separately, controlled
credential-session binding, registration region and origin, region-evidence hash,
finished year/quarter, and an optional separately reviewed download-host list.
The default is an empty list: the real host is currently unknown. It omits
the optional bill `type` filter so the intended request covers all types. The
account/quarter scope hash excludes the credential session: changing sessions
must not create a second durable apply claim for the same account-quarter.
The private journal contains account identifiers and must be kept out of Git,
reports, logs, Notion, screenshots, Docker context, and final bundles.

An apply claim is an uncertain one-attempt tombstone contract. The acquisition
module performs no filesystem or database writes: it cannot safely choose a
private storage root for exact UID/session data. It supports bounded canonical
serialization and hash-chained replay. The separate DB0022
`AccountBillArchiveClaimRepository` now inserts that claim under the existing
UID-scoped ledger lock, with an account/quarter primary key that excludes
credential session and settlement currency. A second session cannot overwrite
the immutable claim. The repository commits first and reads it back in a new
transaction; a missing or changed readback fails closed. A known or ambiguous
exchange response cannot turn the claim into permission to retry. The DB code
has no HTTP or signer path. A future sender still needs controlled credential
ownership and a separately reviewed exact endpoint allowance before any POST.
Readback checks `apply_claimed.completed_at <= db_recorded_at <= readback_at`
with no allowance for a future-dated claim, and requires DB insertion within
two minutes of the host claim timestamp. If this fails after commit, the
one-attempt row remains and no retry is authorized.
Database open, lock, insert, commit, and readback failures return fixed
`archive_claim_storage_uncertain` or `archive_claim_readback_storage_uncertain`
codes with no chained SQL exception; neither path clears a committed claim or
permits a retry. An ordinary duplicate retains `archive_claim_already_exists`,
and a proven clock mismatch retains `archive_claim_readback_clock_invalid`.
An unavailable or invalid pre-claim host clock returns the fixed
`archive_claim_clock_source_invalid` before any DB session or apply attempt.

DB0022's actual PostgreSQL migration, trigger, concurrency, restart, and
downgrade behavior remain **unverified** in this revision because the isolated
Docker daemon was unavailable during the focused work. The migration refuses
downgrade while any claim row exists. Unit contracts and static migration
identity do not substitute for PostgreSQL execution; no durable-claim
production acceptance is asserted yet.

The final hermetic runner now selects the DB0022 PostgreSQL claim tests and
requires exact passing concurrent/restart and future-host-clock claim cases,
plus an exact passing nonempty-downgrade retention case. Its empty-database
path upgrades to the current head, downgrades to 0016, upgrades to head
again, and compares migration identity and drift. These checks are wiring
only until a fresh
isolated PostgreSQL run actually passes on the final source revision.

A status response can be recorded from the untouched plan as an isolated GET
diagnostic. Once any status is recorded, this journal refuses an apply claim;
the recorded status is **not** a status-first runtime eligibility check. A
missing, failed, or unsupported GET is not modeled as an archive absence and
does not create permission to apply. When the host pin is empty, ongoing/failed
states can be recorded, but a finished response containing `fileHref` is
rejected. Supplying a host from that same response would not be an independent
pin.

OKX's [regional API guide](https://app.okx.com/docs-v5/en/) documents
`POST /api/v5/account/bills-history-archive` as **Read permission**, but the
POST creates or reuses a server-side archive job. It must not be classified as
a retryable GET or added to the trade/maintenance POST allowlist. A false apply
result means generating, not empty; the guide says to check after two hours,
possibly longer at peak demand. The GET on the same path returns
`finished`/`ongoing`/`failed`. The temporary `fileHref` expires after 5.5 hours;
a previously applied quarter can be reused within 30 days. The current regional
guide shows one apply per 10 seconds, while an older regional snapshot showed
12 per day; any eventual runtime scheduler should conservatively satisfy both
limits. The current quarter is excluded, and data begins on 2021-02-01.

The [regional change log](https://app.okx.com/docs-v5/log_en/) describes the
archive endpoint as released in production; generic Demo API documentation
warns that some functions are unsupported. Specific Demo archive availability
has not been verified. The archive applies to unified-account data only.
Absent, unsupported, failed, incomplete, or conflicting responses remain
unknown. `ts` in apply/status is the server's **first request receipt**, not the
current GET response time; the journal records current request clocks separately.
CSV `ts` is balance-update completion, neither fill time nor funding accrual.

The official guide's example labels “2024 Q2” with the interval
`[2024-07-01, 2024-10-01)`, which conflicts with calendar Q2. The existing
offline parser enforces calendar-quarter row bounds and rejects a conflicting
file; the diagnostic journal does not reinterpret it. The guide also limits
its stated interval rule to files generated after 2024-10-11. No archive can
prove lifetime streak, high-water mark, or complete quarterly coverage merely
because its ZIP parses.

The guide provides a placeholder URL, not a verified production download host
or scheme. A future downloader must have an independently reviewed exact host
pin, TLS verification, no redirects or proxy substitution, bounded streaming,
and must never send OKX auth headers to the signed file host. The temporary
URL is never serialized by this journal; only its digest is retained. This
means the journal alone cannot re-download or authenticate source bytes.

An offline `record_supplied_download` transition now binds a caller-supplied ZIP
to the exact digest of a previously observed `finished` link. It requires an
ordered request/header/body clock, HTTP 200, zero redirects, and a request no
later than 5.5 hours after the link observation. The existing bounded ZIP/CSV
parser then checks the quarter, required columns, numeric balance changes, and
strictly descending bill IDs. The resulting hash-chained journal records only
the URL digest, ZIP/CSV/rowset digests, size, count, and hashed endpoint bill IDs;
it omits the temporary URL and private rows. Replay rejects a mismatched link
even if the event hash was recomputed. These are **supplied, unowned bytes**:
the new state is `archive_bytes_unverified`, and account completeness and
execution authority remain false. No authenticated request, download, source
custody, quarter coverage, or Demo support is established by this transition.
