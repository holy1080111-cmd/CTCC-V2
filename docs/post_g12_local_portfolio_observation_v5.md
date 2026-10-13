# Post-G12 local portfolio observation V5

`publish_capture_inspect_local_portfolio_v5` is a read-only, DENY-only
diagnostic. It calls the existing V4 entry in the same invocation, reopens its
immutable event receipt and observation sidecar, then makes two separate
PostgreSQL `read_portfolio_checkpoint` calls under the exact Demo account UID.
Each call reads all settlement currencies and the legacy local state. The V5
receipt is published to its own pre-provisioned no-clobber root and read back;
it contains hashes and fixed blocking codes, never the raw local checkpoint,
credential, UID or SQL detail. An old V4/V5 receipt cannot resume a run.

The two observations must have the same canonical local-state hash, exact
account/environment/currency, and strictly ordered UTC times after V4's event
read and before the original candidate deadline. A changed state, unknown
scope, clock conflict, failed read, corrupt V4 evidence, cancellation or late
V5 publication failure cannot create an authority claim. Already-published V4
and V5 bytes are never removed to make a later step pass.

The reader validates the nested all-currency ledger, unresolved intents, and
legacy state shape and requires a bound 64-hex credential-session ID when a
legacy reconciliation checkpoint exists. Missing fields or an active exposure
omitted from the blocking reasons reject the checkpoint. An empty
blocking-reason list can produce `local_flat_at_reads=true` only for the two
sampled, internally
consistent local checkpoint reads. Matching hashes do not exclude an
intervening write that was reverted between reads. They do not prove
exchange-wide flatness,
future writer exclusion, historical completeness, authenticated original G1–G11,
complete post-G12 execution recheck, reservation, intent or submission
eligibility. All those flags remain false. Source-owned original qualification
still stops at G1–G5, and the V4 input remains caller-origin.
