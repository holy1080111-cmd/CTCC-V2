# V7 locked recorded EOF-to-DB-clock delta diagnostic

`AccountCurrentHistoryRecencyRepository.read_joined` is a separate v2,
read-only companion to the original B3/DB0038 v1 observation join. It leaves
the v1 policy and receipt bytes unchanged. The caller pins the exact latest
history and current index heads; the companion independently obtains the v1
DENY readback, then acquires a fresh PostgreSQL exact-UID transaction lock.
Under that lock it checks both heads and the local ledger checkpoint again,
rereads the original V7 B1 current page chain, and replays the eight-algo
current-source policy and measured body-EOF witnesses. Only after that work
does it query native PostgreSQL `pg_catalog.clock_timestamp()`, so a session's
`search_path` cannot substitute a same-named user function.

The new receipt reports whether the *recorded timestamp* of the oldest
replay-verified B1 body EOF is at most 30 seconds before that DB sample. Each
page must have causally ordered request, headers, measured EOF, response-close
and DB timestamps; future,
missing, reversed or overlapping page times cause rejection. Exactly 30
seconds is within the bound; 30 seconds plus one microsecond is stale. This is
an ephemeral cross-clock delta, not proof that the captured page was actually
fresh or that the exchange clock, host clock or transport registration was
authentic. B1 recorded times use an injected host callback without a same-call
native host/monotonic attestation; a host-clock correction can make an old page
appear close to DB time. The v1
`current_freshness_at_locked_readback_unproven` blocker remains in the v2
receipt even when the recorded cross-clock delta is within the bound.

The receipt contains no raw account pages or exact UID. Source authenticity,
complete history, funding, portfolio risk, flat-start permission and execution
authority remain false or unknown, and admission is always `DENY`. It is not
persisted as an account revision and cannot be passed to an order route as a
permit. Unit tests cover the 30-second edge, future and malformed times.
Integration tests cover a second UID-lock wait after v1 readback, a local
checkpoint change between the two transactions, and a public-schema time
function shadow. They require an explicitly supplied, migrated, isolated
PostgreSQL `DATABASE_URL`. No private exchange request is made by either test.
