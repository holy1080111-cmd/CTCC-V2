# Independent reporting for the current manual Demo tool

The manual profile can explicitly enable `submission_outbox_enabled` and
`notion_outbox_enabled`; both default to false. Notion additionally requires the
submission worker. Enabled workers start only after genuine manual startup has
confirmed physical DB0058 and completed recovery. Failed or older startup
does not launch them. Shutdown stops partially started workers too.

The submission worker uses three fresh SQL engines on its own thread/event
loop. All three share a newly read, independently pinned CA context and the
same explicit database route, with distinct writer/custody/frame-reader roles.
The actual complete58 catalog and exact grants must be observed. No caller
factory, copied witness, local report flag, old trading owner or monitor result
can supply the nonconstructible same-task reporting lease. No exchange client,
Arm or trading callback is part of this path.

The existing native projector replays each committed spool and its exact raw
journal/closure/forensics before publishing its local pair. It then verifies
the accepted pair and projection receipt in another transaction. Every pass
first audits previously accepted projections. Missing or changed accepted
files fail closed; they are not silently replaced. Sharing failures and other
projection failures retain the spool and accepted journal, report pending and
never submit an order. A failed pass closes its own reporting lease so a later
pass can obtain a new physical role/server observation after a restart.

The existing task-bound, one-use Notion issuer has a separate explicit58
projection reader. It checks the actual58 factory and full catalog, performs
two distinct committed transactions, and binds the immutable spool,
projection receipt, local payload and configured destination. All owned SQL
engines close before a prepared delivery permit is returned. The existing
destination-property readback, private token handling, final HTTP authority,
outbox claim, idempotency, retries and accepted-journal rules remain in place.
Notion failure cannot alter a trade result or create another exchange order.
The original55 issuer/catalog behavior remains separate and unchanged.

The positive original-OCO public58 integration case includes a fresh configured
child process that exercises actual TLS roles, SQL-to-file projection and
the two committed Notion-source readbacks. Its raw trading data and destination
are declared synthetic; it sends no Notion request and creates no exchange
order. Local collection is not a PostgreSQL, real Notion, Demo or Live pass.
