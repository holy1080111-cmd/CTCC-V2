# PostgreSQL component validation

The current CI contract uses two PostgreSQL components and the existing eight
Linux nonPG shards. Windows retains its separate eight-shard union. The required
status remains `Docker hermetic regression`.

PostgreSQL ownership is by complete module. The controlled-writer coverage and
account-history-partition modules always belong to component0. Other PG modules
use the first eight SHA256 bytes of their exact POSIX path, big endian, modulo2.
The source currently declares65 PG modules; static inspection yields613 cases,
with33 modules/345 cases on0 and32 modules/268 cases on1. Actual collection and
execution must confirm the exact source; these static counts are not results.

Component0 owns all preflights, the actual0048/0049 child migration receipts, the
0050 receipt and retained rows in the same primary0051 database, before/denial/
after nonempty downgrade readback, the isolated0047 predecessor experiment and
synthetic process/PG/Redis/API restart evidence. Those stateful experiments must
not be split between workers or reconstructed in a fresh database.

Component1 runs its selected cases after the same source/schema/dependency
checks and records that anchor experiments were not performed. It cannot claim
anchor receipts or restart acceptance. Each component has one main PG invocation
and its own fresh credential-free isolated PG/Redis/internal network.

Both collect the entire ordered test inventory, record selected and observed
parameter IDs, and write strict JUnit. The aggregate requires exactly two PG and
eight nonPG artifacts from the same source, checks every selected JUnit class/
parameter identity, and proves disjoint full coverage. All23 required PG cases
and65 required modules must be owned exactly once across PG0/PG1. Auxiliary
preflight and predecessor reports do not increase the full-suite unique count.

The PG case-stage cap remains7200s, overall driver budget9900s, cleanup budget300s
and GitHub job cap180min. Failure, timeout, missing report or uncertain cleanup
remains failure. Historical timings and old CI/test counts cannot establish this
version's acceptance. The current diagnostic consumer pins the exact final
hermetic producer while retaining its separate reviewed traceback helper pin.

The private post-common driver function is called directly by `main`; its actual
PG0/PG1 routing, command options and original try/finally are covered by prepared
synthetic IO tests. These tests do not run Docker, PostgreSQL, exchange traffic or
orders and are not native acceptance. Formal acceptance requires fresh matching
CI and independent raw-artifact readback.

Trading remains disabled. This validation partition creates no source, account,
G6, risk, order, Demo or Live authority and makes no profitability claim.
