# Original final evidence examples

The original [CTCC complete implementation directive](https://app.notion.com/p/3d832165a6888173bfb1df896604fc7c)
was read again on 2026-09-28. Its final-output section requires these four examples:

1. 一筆被擋掉的舊式高分錯誤訊號完整 evidence。
2. 一筆新流程通過 qualification 的 Demo 候選完整 evidence。
3. 一筆因 evidence 生成期間價格跑掉而被 Execution Recheck 取消的案例。
4. 一張 summary.png，清楚畫出 Entry / Timing / SL / TP / liquidity / Gate 結果。

The current master directive requires genuine retained sources. Synthetic fixtures
verify implementation and cannot satisfy any example. Neither a generic denial
nor a stale account failure substitutes for the first or third specified case.

| Example | Required lineage | Current acceptance |
| --- | --- | --- |
| Old high-score error rejected | Preserve the actual old signal/score and the source-derived qualification failure, with the original candidate geometry and complete evidence. | NOT EXECUTED |
| New qualified Demo candidate | Same owned original candidate through source-derived gates, actual G12 publication/readback and the new qualification pipeline. Caller-supplied PASS is not evidence. | NOT EXECUTED |
| Price changed during rendering | Original geometry/event/zone, measured rendering and publication times, genuinely fresh post-barrier market data and the resulting economics/continuation rejection. No deliberate timestamp or market-byte alteration. | NOT EXECUTED |
| Complete summary image | Inspect the actual immutable PNG and its hashed report. Entry, Timing, SL, TP, liquidity and gate results must be readable and source-bound. | NOT ACCEPTED |

Visual inspection of the native Range V5 synthetic summary found readable entry,
timing, stop, target and gate claims, but no explicit liquidity section in the
summary itself. Liquidity in the separate timeframe images does not close this
specific requirement. An explicit new `ctcc-pillow-evidence-liquidity-v2` display
profile now renders each panel's actual surviving liquidity inventory, known-at
and as-of times; empty detected inventory is named separately from current
survival, which still requires recheck. Overflow is marked and the complete
original source remains in the hashed report. The old v1 default remains intact.

`render_evidence_liquidity_v2` and
`publish_qualification_evidence_liquidity_v2` use the existing snapshot validation,
G1–G11 replay, immutable six-file publication and readback. They do not change the
candidate geometry, event, expiry, risk policy or authority. Their twelve targeted
tests pass on the frozen source, including actual native publication/readback,
report-size enforcement after liquidity overflow, and rejection of a repeated
receipt; four old fixture packets (24 files) remain byte-identical. Broader
regression and the final exact-source suite must still finish. All these fixtures
are synthetic: the actual-source final example remains NOT ACCEPTED, and the
new owned trading coordinator has not yet adopted this profile.

The twelve-case run completed after the host's fan diagnostic under verified
single-CPU affinity and a 45-second process limit, with zero failures or skips.
Its XML is `summary-v2-bounded-after-fan-check-20260928/target.xml`, SHA256
`268662f653a6ab041611b374ef03e1617ff93ff228b6dee3851fcbd7c56a38d9`.
The interrupted broad renderer runs remain incomplete; this focused result does
not promote them to PASS or establish long-running thermal stability.

The connector reported the original page's last edit as
`2026-09-12T16:13:18.378Z` and verification state `unverified`; it did not report
truncation or unknown-block counts. These unavailable fields are not asserted to
be zero. A fetched specification is a requirements reference, not market
Point-in-Time availability evidence or account/trading acceptance.

The [V2.0 execution report](https://app.notion.com/p/3da32165a688817ab205dea8b1592424)
continues to permit sealed Historical OOS. Its older dates and uncompleted
checkboxes do not demonstrate that current engineering or research has passed.

## 2026-10-05 direct Notion source re-read

The original implementation directive was fetched directly from Notion again:
[CTCC 遠端控制｜一次貼上完整版施工指令](https://app.notion.com/p/3d832165a6888173bfb1df896604fc7c).
Notion reports its last edit as `2026-09-12T16:13:18.378Z` and verification as
`unverified`. The original four examples remain exactly the four listed above:
blocked legacy high-score error, qualified new-flow Demo candidate, price-change
cancellation by Execution Recheck during evidence generation, and a readable
`summary.png` with Entry / Timing / SL / TP / liquidity / Gate results.

The newer V2.0 execution report uses a different four-category grouping
(PASS candidate, WAIT/CANCEL/FAIL candidate, stale/changed-market rejection,
and execution/reconciliation). It does not replace the original output list.
For the current directive, evidence must satisfy the original list and the
newer real-source sample requirements; one artifact may satisfy both only when
its actual lineage proves both. No synthetic fixture or the current G1
historical diagnostic counts. All four original examples remain NOT ACCEPTED.
