# Original Notion requirement: four real, reproducible examples

The original [CTCC remote-control implementation instruction](https://app.notion.com/p/3d832165a6888173bfb1df896604fc7c?pvs=204)
requires these four distinct examples. This list records that source requirement;
it does not substitute synthetic fixtures or an offline replay for observed
Shadow/Demo evidence.

1. An old-style high-score but invalid signal rejected by the new qualification
   pipeline, with complete evidence.
2. A Demo candidate that passes the new qualification pipeline, with complete
   evidence.
3. A candidate canceled by Execution Recheck because the market changed while
   its evidence was rendered.
4. A `summary.png` visibly showing Entry, Timing, SL, TP, liquidity and Gate
   results.

As of 2026-10-16, the original requirement remains **0/4 observed examples**.
The current synthetic unit fixtures, computational G12 receipts and public
timestamp diagnostics do not meet it. The authenticated Demo account source and
production qualification/recheck/submit chain remain closed; no example may be
marked complete until its raw source, lineage and immutable artifact hashes are
read back from a real controlled run.
