# Exact-source0058 parallel validation

The source0058 full suite has five independent PostgreSQL/Redis components
and eight non-DB shards. Source0056 and0057 retain their original topologies.
No test is omitted. The final union independently requires the same full
collection in every component, disjoint selected node IDs, original passed
JUnit identities, every mandatory case exactly once, matching source/tree/
manifest/dependencies/migrations and complete cleanup evidence.

Components0 and1 retain their original migration/restart anchor and historical
receipt producer modules respectively. Component2 owns all actual0058 and0057
successor modules, including the genuine0058 stopped-state setup. Components3
and4 take deterministic whole-module halves of the remaining older suites.
They emit their own actual component indices, selection records and JUnit;
they neither borrow historical receipts nor acquire runtime authority.

This follows run44746035781 on source9df7553, where component1 reached564 of570
tests but exhausted the unchanged7200-second stage budget before final JUnit
publication. That run remains FAIL. Its partial progress is not acceptance.
Parallelization does not extend a market/account/request expiry, increase the
stage timeout, skip failures or turn missing reports into successful results.
The final modified source still requires a new complete CI union.
