# Original-source financial operands for account V8

`account_history_risk_v8.replay_account_history_risk_v8` adds a conditional
financial reducer for the future complete account assembler. It replays
the original pinned B1 source population and B3 index through the existing
FIFO lifecycle reducer, then independently derives the versioned typed
history index. Historical B1 packets, `_BASE_GAPS`, B3/B5 policies and receipt
bytes are unchanged. This implementation neither creates a migration nor
performs network, credential, database or order operations.

The new reducer can return a concrete `reconciled_closed_run_net_pnl`.
For that result it requires all of the following source relationships:

- Strictly ordered, structurally closed original fill inventory with the
  existing independent FIFO gross-PnL and signed per-fill fee reconciliation.
- An original nonzero position response during that holding. Its exact
  `posId`, instrument, position side, margin mode, settlement currency and
  creation time must identify a unique historical position. Its own source
  update time and signed quantity must agree with the known execution-time
  inventory; an update exactly coinciding with a fill remains ambiguous.
- A separately pinned positions-history archive, including its original
  config-before/config-after bodies and complete `uTime` page chain. Its exact
  UID, main UID, modes, currency, session and claimed route must match the B1
  population. A matching archive hash proves retained-byte integrity, not
  native acquisition or registration-site provenance.
- A unique normal full-close SWAP row with matching maximum inventory,
  total closed quantity, direction, weighted entry and exit prices, gross
  PnL and fees. Its recorded net must exactly equal gross plus signed fees,
  aggregate `fundingFee` and liquidation penalty. Normal closes require
  an explicit zero penalty. `settledPnl` is documented for cross FUTURES;
  an explicitly empty SWAP field is inapplicable, while an absent/null
  value or a nonzero value leaves the result unknown.

The [official positions-history contract](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-positions-history)
was rechecked on 2026-10-22. It provides aggregate position funding and net totals. The reducer uses those
totals to corroborate a closed run. It does not turn position `uTime` into
its closing execution time: `closed_at_execution` comes from the final
original zeroing fill's `fillTime`.

The same contract allows a position ID to be reused until more than 30 days
after its last full close. A position ID alone therefore cannot join a trade
episode. The reducer additionally requires matching creation time, a live
source witness during the holding, exact fill inventory, quantities, prices
and financial totals. Multiple matching historical versions remain ambiguous.
The supported financial mode is explicitly `acctLv=2`/`net_mode`; hedge-mode
position quantities cannot be interpreted using signed net-position arithmetic.

The new receipt keeps three time domains separate. Typed fill cashflows retain
actual execution time and the fill record's source-generation time. Account
bills retain their original balance-update/posting `ts`, without inventing
funding accrual time. Optional public settled-funding sources are reopened
through `settled_funding_history_source`; their `fundingTime` events remain
public settlement candidates. No nearest-time or same-amount join establishes
that an event caused an account bill. A settlement exactly at a fill is
explicitly ambiguous. Aggregate closed-position funding can reconcile while
individual account accrual events remain unknown.

A holding can span reporting cutoffs. The aggregate closed-run net must not
be assigned wholesale to a UTC daily or rolling seven-day loss window merely
because its final fill closes inside that window. Source-time cashflows and
funding accrual coverage remain separate requirements for those risk totals.

All original account bill movements and unmatched position-history rows remain
in the receipt, including foreign-currency bills. No unsupported movement or
unjoined row disappears to make an account-wide loss window appear complete.
The reducer rejects reuse of one historical position total across multiple
runs. Duplicate public sources or malformed/rehashed source archives are
rejected by original-source replay, without accepting a supplied result object.

`observed_positive_net` reports the sign of a reconciled run only. It cannot
be used as a complete account-wide positive reset or loss-streak seed.
`loss_streak_at_history_start` remains unknown until actual source-derived
account-wide continuity is established. The reducer has no account-complete,
snapshot, owner or execution-authority API. It cannot repair the currently
missing native current-account handoff, exchange-wide history tail/finality,
retention, account-wide funding attribution, persistent loss seed, or preserved
drawdown-window/HWM proof. The existing risk window/genesis is never reset.

Registration-site provenance remains unverified. A future operational-route
proof must be a distinct contract, never a relabelled registration claim.
This financial prerequisite does not complete R5, G11, R6, R7 or Demo acceptance.

Focused tests use declared synthetic raw source responses and replay their
original B1/B3/positions/funding page chains. They demonstrate arithmetic and
rejection behavior only; test authoring or a synthetic positive amount is not
authenticated Demo evidence. Exact test results are recorded by the parent
validation harness.
