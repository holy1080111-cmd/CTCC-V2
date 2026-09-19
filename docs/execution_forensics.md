# Source-bound execution cost decomposition

`app.trade_evidence.execution_forensics` replays the existing v1 forensic result
and creates a separate versioned receipt. It never changes the original v1
candidate, input, result or bytes. The new receipt binds the exact base payload,
base result, candidate, declared reference-age policy and every quote claim.
Readback recomputes the base and all derived metrics; a rehashed caller result
cannot substitute for replay.

The extension computes net R against original planned cash risk, closed holding
duration, paid fees and received rebates separately, cash deviation from planned
entry and source-bound exit references, original stop/target distances, and
entry/exit spread and residual slippage. All arithmetic uses exact rational
operands with the existing Decimal presentation. Improved fills retain negative
slippage; fees and rebates are not conflated by taking an absolute value.

Each execution reference has an exact fill/report/candidate/account/instrument
binding, source hash, bid/ask and causal request/source/receipt timestamps. Its
source age at the fill must satisfy the policy. A later receipt, stale source,
missing fill quote, missing fill history or unconverted fee currency leaves the
affected metric unknown. A fresh receipt does not refresh an old source timestamp.
An open position has no final holding duration or realized net R. Duplicate,
conflicting and unbound references are rejected without deduplication or repair.

For a buy, the executable touch is ask; for a sell it is bid. Price friction
relative to quote midpoint decomposes exactly into half-spread plus residual
slippage from the executable touch. Quote-reference gross PnL is reconstructed
only for a closed position with complete references. The accounting identity is:

```text
quote-reference gross PnL
- spread cost
- residual slippage
+ signed fee flows
+ signed funding flows
= actual-fill net PnL
```

Actual-fill gross PnL already includes spread and execution-price effects.
Subtracting those components again from actual-fill PnL would double count
costs, so the verifier requires exact equality with the original cash result.
Planned-entry and exit-reference deviations are explanatory measurements and
are not deducted again either.

These are computational source claims. The extension does not authenticate
exchange records, prove ingestion completeness or funding accrual, reconstruct
missing strategy/regime/score/leverage lineage, or create a real Demo/Live sample.
The original raw-account replay continues to mark unproven coverage incomplete;
feeding that result here cannot promote unknown PnL into a value. The eventual
durable outcome owner must pin qualified intent/report lineage and retain true
source quotes before using the extension in a production forensic report.
Internal account-bound receipts are private operational evidence; external
Notion/release projections require the existing redaction allowlist.
