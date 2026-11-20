# Manual tool main0058 startup and journal route

The main ASGI lifespan now accepts a verified0058 return from the original
manual startup/recovery owner, alongside the existing0056/0057 profiles. The
strict restricted-role, source, stopped-account and durable restart-recovery
checks remain inside that original lower startup function. Unknown or malformed
heads still refuse before exposing readiness or manual HTTP actions.

The restricted manual HTTP surface now includes the existing authenticated
`GET /api/demo-automation/native/journal` route. The dashboard's local-journal
button therefore reaches the same original source-owned reader in manual mode.
The route remains GET-only, bounded, token protected and unavailable before
verified startup or after shutdown. Legacy order endpoints remain outside this
surface. Startup never restores an Arm.

Validation:60 Windows tests passed, zero failures/errors/skips, including the
actual ASGI lifespan/router composition,56/57/58 startup sequencing, journal
authentication and unknown-head refusal. Lower source/account seams in these
tests are explicitly synthetic. Actual restricted TLS database verification
belongs to the separate source-bound PostgreSQL tests; these results are not
real account, Demo execution or final deployment acceptance.
