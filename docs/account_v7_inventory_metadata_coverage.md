# V7 observed inventory instrument metadata

The V7 declared-metadata receipt checks only the SWAP instruments named in the
capture plan. An open position or pending ordinary/algo order can name another
instrument. The new `observe_v7_inventory_metadata` diagnostic replays the
original V7 raw/page chain and its current-source and declared-metadata
receipts, then enumerates the instrument IDs actually seen across the
positions, ordinary pending orders and eight pending-algo queries. It retains
only row/page hashes and counts, not private order or position IDs.

For an observed instrument absent from the plan's leverage query, the
diagnostic reports `leverage_instrument_not_declared_or_queried`; it never
borrows the candidate instrument's leverage. It also reports a missing
account-instruments row separately. Empty inventories do not become a
positive metadata-coverage claim. More than 128 distinct observed instruments
abort this bounded diagnostic instead of truncating the set. A saved receipt
requires an independent SHA pin and full original-chain replay for readback.
Its local shape check also requires unique instrument IDs and positive row
counts that sum to the source inventory total, exact hash fields and the
mandatory DENY blockers. Those checks do not authenticate the source.

This is a coverage observation, not an account-wide instrument catalog or a
contract-value/unit mapping. Even a positive observation says only that the
plan-declared metadata rows needed by currently observed exposure passed the
older bounded shape check. The source remains unauthenticated, historical and
local exposure are unknown, and the receipt always carries
`account_complete=false`, `execution_authority=false`, `snapshot=null` and
`admission=DENY`. The V7 capture, current-source and declared-metadata
policies are unchanged. Tests use synthetic response bytes only.
