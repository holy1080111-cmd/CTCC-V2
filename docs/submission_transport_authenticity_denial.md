# DB0042: unverified submission transport cannot enter the outbox

DB0018 made a report spool eligible whenever the associated submission outcome had `status='acknowledged'`. An acknowledged-looking response body alone does not prove the HTTP response came from the exchange. The current private outcome issuer records `transport_authenticity_verified=false` and has no positive issuer.

DB0042 locks the private outcome, spool and projection tables with `NOWAIT`, then refuses the upgrade if any legacy `eligible=true` spool **or any projection receipt** exists. The failed transaction preserves every original outcome, capture, spool, and receipt byte; no row is deleted or rewritten. Such a database requires separate forensic resolution and a reviewed migration. The migration does not send an order or an outbox job.

When the preflight passes, an independent CHECK constraint and the revised spool trigger permit only `eligible=false`. The trigger also requires the persisted outcome JSON to carry a boolean `transport_authenticity_verified=false` and a status matching its stored status. A second CHECK constraint and revised projection trigger reject every new projection receipt. Disabling only the trigger cannot make a spool eligible. Existing ineligible spools remain retained as private evidence.

Downgrade always refuses. Restoring DB0018 would reinstate status-only eligibility, so a future positive transport issuer must arrive with its own reviewed migration and tests. Until then, **outbox positive acceptance remains FAIL** even though durable negative evidence is retained.
