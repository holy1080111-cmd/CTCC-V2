# Live entry transport containment

Live entry dispatch is blocked with `live_qualification_authority_unavailable`
at the shared production execution transport. Configuration flags, the legacy
service Arm, caller payloads and replayed journals cannot authorize new exposure.
The boundary is checked before signing and immediately before HTTP dispatch;
write configuration is also checked at both points. There is no enable switch.

The common private REST runtime owns its HTTP client, disables ambient proxies
and redirects, and sends to the configured absolute origin. External clients
are accepted only as exact, network-free MockTransport test adapters without
auth, hooks, mounted transports, extra default headers, queries or cookies.
This restriction is rechecked after signing. Each request explicitly disables
redirects and client auth, so a maintenance response cannot dispatch a second
POST and a Live read cannot inherit a Demo header from a supplied client.

This closes the direct-client bypass while the new qualification pipeline is
integrated. It is containment evidence, not Micro Live readiness or acceptance.
`MICRO_LIVE_READY` and `MICRO_LIVE_ACCEPTANCE` remain unproven. Synthetic service
tests using fake clients do not establish real Live submission capability.

Authenticated reads and existing cancel-order, close-position, cancel-all-after,
leverage and nonexecuting precheck operations retain their existing service
safety checks. This change performs no exchange request and alters no position
or protection. Maintenance writes remain single-attempt; uncertain responses
must be reconciled instead of retried.

Removal of this block requires the controlled runtime to bind the exact account,
current public/account source evidence, fixed original candidate, atomic risk
reservation, durable request intent and independent readback to a one-shot
dispatch authority. Live additionally requires the operator's contemporaneous
Arm and exact confirmation phrase, dedicated credential verification, flat start,
micro-notional/loss/exposure caps and persistent safety state. Historical Arm or
intent replay cannot recreate authority after restart. Real funds must never be
submitted merely to make a test or acceptance indicator pass.
