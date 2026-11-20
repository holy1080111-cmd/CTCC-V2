# Controlled Micro Live close custody

This source extension is not installed merely by importing it. The sole main
0058 migration must explicitly compose its two tables, procedures and exact
lifecycle function overrides. Actual PostgreSQL acceptance must use that
migration; fixtures must not install the SPEC independently.

The original operator's separate close consent lasts exactly 120 seconds. It
does not restore entry Arm or extend an entry candidate. Closing uses the
original net position, instrument, isolated margin, filled contracts, opposite
side and fixed original SL as a reduce-only FOK bound. Unsupported or missing
source values deny the operation. A fresh complete protection proof must match
the original entry order and original protection client/algo IDs.

The source-owned actor consumes a private one-use intent hook before any await.
The restricted reconciler commits the exact canonical intent and its HMAC,
then reads it in a separate session and verifies the original key's dedicated
intent domain. This stored DATA cannot recreate an owner or submit an order.
There is one intent per original reservation. A second dedicated HMAC domain
guards one irrevocable claim for each of two actions: `close` and
`cancel_original_protection`. Unknown COMMIT, failed independent readback or an
unknown exchange result never causes a retry.

Every intent/claim holds the same account advisory lock, legacy Live lock
1680010 and current Control row through its transaction. Original consent
requires disarmed Control with EStop false. The prepared close pins its exact
settled Control revision, current credential session, process and policy. A
later revision change or Stop revokes both actions, even if the actor has not
yet observed the revocation locally. Clearing Stop to use an old close consent
is forbidden. A future stop-aware close requires a distinct human procedure.

The close claim preserves its parent's exact UTC/monotonic deadline pair. Its
current observation must remain fresh, and the original deadline cannot move.
Only after complete actual fills prove the full close, all positions are flat,
ordinary pending orders are empty, and exactly one pending original protection
algo remains can the separate cleanup action be claimed. Cleanup receives its
own fresh deadline, bounded by five seconds, the original consent and the
current Control lease. It cancels exactly that one original algo. It cannot
cancel protection before closing or cancel another algo.

Both claims remain durable and immutable. Claims alone retain the reservation.
Only a separately authenticated lifecycle proof with complete fresh account and
fill evidence, exact close lineage, no positions, no ordinary pending orders
and no pending algos may resolve it. The original OCO resolution path remains
available. A missing exit kind or missing controlled lineage fails closed.

The dedicated reconciler's exact privilege profile is 21 rights: its prior 17
plus SELECT on these two tables and EXECUTE on the two fixed procedures. It has
no table write privilege, no raw HMAC key read and no entry/Arm permission.
The entry writer's original 17-right profile is unchanged. Catalog verification
pins the actual 0058 head, owner, functions, columns/defaults, CHECKs, keys,
indexes, triggers, collation and effective ACLs. The owner installer must verify
the expanded source-defined schema separately before enabling a configured
runtime.

Repository hooks expected from `app.okx_live.owned_close_v1`:

* `_consume_owned_live_close_intent_v1(owner, key=...)`
* `_consume_owned_live_close_claim_v1(owner, action=..., key=...)`

Each returns only the exact signed raw bytes and original opaque key owner,
after burning its source-owned permission. No caller payload signer exists.
The physical boundary is a separate source-owned component and must recheck
the held Control/catalog/role/account/legacy locks around the actual write.

Synthetic unit and isolated PostgreSQL cases test engineering contracts only.
They are not real venue examples, human authorization or Micro Live acceptance.
Live remains OFF until its actual configured runtime and current human Arm.
