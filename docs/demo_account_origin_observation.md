# Native Demo account-origin observation

The current-only native account capture may return a private, one-use origin
observation alongside its existing DENY diagnostic. The observation is minted
only after the owned signed account capture closes, both `account/config`
responses agree on exact `uid`/`mainUid` and mode, the DB journal is reread,
the companion proof is separately reopened and replayed, and the original DB
chain is rechecked. Its issuance is limited to the existing current V6/global
account path. Historical packet schemas and diagnostic receipt bytes remain
unchanged.

The observation binds the actual signed request's pinned private REST origin,
the TLS hostname checked by the native collector against that request and
retained in successful native journal raw-finalization records, the
`x-simulated-trading: 1` header constructed by the same signed request builder,
both config identities, the copied credential session, packet/plan/proof/readback
digests, and the original short UTC and monotonic expiry. It retains account
identifiers only in the in-process object; they are not added to the diagnostic
receipt, journal, log, or report. A copied receipt, packet, plan, route object,
or caller-provided region cannot mint or renew the one-use handle. A wrong task,
session, process, thread, receipt, changed credentials, expired deadline, or
second consumption burns or rejects it. The registry holds only weak references
to the credential session and issuing task, so retaining a diagnostic cannot
keep credentials or a completed task alive. Older journals without a recorded
TLS hostname still replay under their existing contract but cannot mint this
new observation.

The controlled session registers an in-memory digest of its copied credential
fields in a private weak-key registry outside its mutable slots. Native claim,
collector adoption, origin issuance and one-use origin/raw-packet consumption
compare both the current fields and the session-local digest to that original
construction-time pin. This detects an in-place change to a frozen credential
object even if a caller also rewrites the session-local digest. A failed claim
burns the session; a failed consumption burns the lease. The digest is not written to
the journal, proof, report or log, and this local integrity check does not
verify provider permission, IP binding or account registration.

The signed request builder check proves which header the controlled client
constructed, not that the exchange separately acknowledged that header. A
successful authenticated read on a pinned host is not an independent statement
of the account's registration region. Accordingly the returned object always
has `registration_region_verified=false`,
`public_source_authenticity_verified=false`, `execution_authority=false`, and
`admission=DENY`. The native V2 public capture retains its unconditional
pre-I/O hard DENY. The existing original-source coordinator still attempts
public capture before account capture; this observation does not change that
ordering or issue a public-source capability.

The missing registration proof must come from an authenticated first-party
account/profile or API-key view, or an OKX support record, that explicitly
names the registration site and is bound to the exact account UID and controlled
credential session (only a nonsecret key fingerprint may be retained). A
configured REST URL, local timezone, operator locale or signed HTTP 200 is
insufficient. After independent proof, a new one-use read session must request
`account/config` on that site's reviewed Demo origin, with
`x-simulated-trading: 1`, verified TLS, no redirect, and exact UID/mainUid
readback. This establishes a route/identity prerequisite only; history,
portfolio, protection and execution completeness remain separate gates.
The [OKX regional overview](https://www.okx.com/docs-v5/en/#overview) and
[Demo services](https://www.okx.com/docs-v5/en/#overview-demo-trading-services)
were checked again on 2026-10-08. The documented `account/config` response
does not carry a registration-site field, so it cannot supply that independent
proof by itself.

The unit tests use synthetic account/TLS-labelled proof replay and no real
credential or network I/O. They test local lifetime and source-binding failure
paths, not a successful authenticated Demo account read. Real account identity,
credential route, and any external registration evidence still require a
controlled authenticated Demo read; full public transport must then enforce the
same Demo REST/WS route and simulated header at every I/O edge before the
separate source-authority review can change.
