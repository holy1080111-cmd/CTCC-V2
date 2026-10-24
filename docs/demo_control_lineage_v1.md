# Complete Control journal lineage V1

This is a read-only foundation for future recovery. It does not clear EStop,
acquire/renew an owner, request Arm, complete an account, reserve risk or permit
an exchange write. The existing Control transitions, DB0019 guards, DB0048 and
its 59c catalog seal remain unchanged.

The pure replay accepts exact original journal columns in their observed order.
It requires every revision from 1 through the target, canonical event/state
bytes, row/event/state hashes, predecessor and unique command IDs, exact account
scope, timestamps and action semantics. It replays original acquire/advance
rules, plus the existing expired-owner safety tightening semantics. A genuine
new owner after expiry/release is historical provenance; it does not give a
previous owner the new epoch or make a saved event a current permit.

Limits are fixed at 4096 rows, 4 MiB of event JSON and 12288 bytes per event.
Missing genesis/middle/tail, duplicate/reordered rows, resource overflow, extra
actions, bool/int aliases, altered pins/leases/epochs or a mismatched target
fails closed. No suffix, sorting, deduplication, cursor restart or fabricated
genesis repairs an incomplete population.

DemoControlRepository.verify_historical_binding now checks the complete prefix,
so existing Control-bound ledger and reporting callers do not accept a lone
event plus an unverified predecessor digest. It returns diagnostic lineage only.
The repository's existing read_in_transaction invokes that full check while
holding the original account advisory and Control-row locks.

read_lineage uses an exact ordinary native SQLAlchemy/asyncpg factory. It reads
the actual locked current head and selected complete prefix, closes that
transaction, then performs a new native-session readback. Count and total byte
bounds precede bulk source retrieval. The current head, complete target prefix,
factory identity and separate actual transaction IDs must remain consistent.
An append between captures denies; it is not retried or adopted. Database
read timestamps come from the actual query, not the caller's legacy clock.
Queries use fixed one-second statement/lock limits and READ COMMITTED.

An optional historical revision still requires a complete prefix from revision1;
the actual full current lifetime is validated too, so a historical request
cannot conceal an invalid later tail. Its target state is explicitly separate
from the currently observed head. A
portable result or caller-provided expected hash grants no ownership or recovery
authority. Native reads do not prove least-privileged deployment, source TLS,
the host clock, current financial populations, reconciliation or protection.

The legacy read/ControlObservation remains a nonauthorizing head observation.
Its snapshot logic stays unchanged so full-history size limits cannot prevent
the existing safety-tightening path from persisting EStop/disarm. Code requiring
complete provenance must use the full lineage checks; it cannot label the old
head observation a complete-history proof.

New unit falsifications and authored real PostgreSQL cases cover complete
cross-epoch replay, source corruption, prefix loss, rehashed semantic changes,
bounds, a raced current head, separate transactions and out-of-range history.
They require actual execution on the final source. No native/PG acceptance is
established merely by authoring these tests.
