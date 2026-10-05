# B6-A original account history diagnostics

This slice produces an immutable, deterministic **DENY diagnostic** from the
original bounded B1 ingestion journal and DB0021 observation export. It does not
produce a PortfolioRiskSnapshot, register an account revision, grant a native
owner, enable submission, or prove full financial history. Synthetic tests of
these mechanics are separate from authenticated account acceptance.

`app/trade_qualification/account_lifecycle_history.py` accepts only original
`JournalReadback` chains and `CaptureReference` objects, the exact account scope
and UTC window, complete presented DB0021 sequence-one prefix, and pinned source
set/index head/policy. No completeness, funding, fill role, genesis, streak,
candidate or native-clock override is accepted. It makes no IO or clock calls.

## Original-source replay

The producer verifies each original journal event/readback and replays B1/B2/B3
using their existing versions. It checks the presented DB0021 document chain,
original row/query membership, exact witness sequence/hash, cumulative counts,
continuation anchors and pinned head. Index counts and locator indices require
exact integers; a boolean cannot satisfy an integer join. Caller-selected pins
alone do not establish DB authenticity; `source_authenticity_verified` remains
false until the later private, separate-session native coordinator binds them.

B3's `source_index` deliberately omits raw row bytes. The producer retrieves the
original canonical row through its verified capture/page/row locators and checks
the page receipt, original body hash and every row hash. It does not accept a
caller-supplied raw row, mutate a captured packet, repair source ordering, or
substitute an account DTO for an original page. Conflicting row variants and late
findings remain evidence and prevent an observed total from being admitted.

The prefix budget is at most the original 32 captures, 16,384 normalized rows,
64 MiB original journal/source bytes, 64 MiB presented index bytes and 8 MiB
diagnostic bytes. A longer required prefix is denied; 32 is not a lifetime limit
or permission to truncate the prefix. This bounded full replay is an offline
diagnostic. It has not demonstrated execution within a native 30-second expiry.
A future coordinator must check its original UTC and monotonic deadlines after
all required source/proof/local-state readbacks and deny on expiry. It cannot
extend a token lifetime or reuse a cached current snapshot to fit this replay.

## Financial and inventory meanings

Observed financial movements reuse B3's unique original signed `fillPnl` and
`fee`, at actual `fillTime`, with original currency/spec checks and locators.
Signed rebates remain credits. A matching original type-2 bill is reconciled
against that fill and retained as a mirror, not added again. An unclassified or
unreconciled bill remains unknown and blocks the observed aggregate. A computed
fill-only subtotal, including a source-derived zero subtotal, is never net PnL,
UTC daily loss, rolling-seven-day loss or a streak seed.
Original B3 window endpoints remain inclusive and retain their original policy
pin. This slice does not implement the later FinancialV2 rolling-7d exclusive
lower boundary or relabel an observed B3 window as an accepted risk window.

Supported structural inventory is limited to original level-2 net mode, linear
USDT SWAP contracts with unchanged source metadata and multiplier one. An original
order's exact instrument/side/position-side/client-ID and boolean `reduceOnly`
join supplies its role. Missing or conflicting role evidence stays unknown;
order retention is not declared complete. The starting anchor retains the
original packet, position-risk page and all observed empty inventory page
receipts. It must have been observed before the first mapped fill. This is a
bounded observed flat anchor, not exchange-wide atomic flatness, account genesis,
or proof that no external actor traded.

`app/trade_evidence/inventory_math.py` holds the existing exact Fraction/FIFO
arithmetic. Only the old `forensics._inventory` definition delegates to it; all
other original file bytes and AST remain unchanged. Original fill order,
precision, state list/tuple shapes and fixed arithmetic error codes are retained.
The history diagnostic orders only a derived view by genuine fill time; original
pages remain unchanged. Equal-time inventory ambiguity, an unknown entry,
over-close, direction conflict or reopen denies lifecycle reconstruction.

The producer labels a source-observed zeroing fill only when mapped inventory
reaches zero and the calculated supported gross reconciles to original fill PnL.
It never substitutes order creation, bill generation or observation time for
that fill. It does not create a candidate/report/submission ID to turn an
untracked exchange trade into a tracked CTCC trade. Full account-wide completed
outcome order and multiple same-instrument lifecycle segmentation remain future
work; a reopen currently denies rather than guesses a new lifecycle.

## Required unknowns

Funding effective accrual and attributable amount remain unknown. Original
account bill `ts` is retained only as generation/update evidence. It is not a
settlement event. An empty bills population, empty arithmetic funding-time tuple,
current forecast or supplied event object cannot prove zero applicable funding.
The root separately retained current global official documentation identifying
funding-history `fundingTime` as settlement time and `realizedRate` as actual;
this slice does not add that endpoint or register a settled-event source version.
A later reviewed original raw collector must bind settled events, complete actual
holding inventory, signed bills and delayed generation before full net can exist.
It must also preserve the limited documented history retention and exact cursor
semantics. Fee-group documentation changes do not introduce a default production
fee; these diagnostics use only original fill fees and currencies.

Strictly positive complete **net** outcomes and a complete account-wide suffix
are required to prove a pre-window reset. Alternatively, genuine account/risk
genesis and complete supported history since genesis are required. Neither is
available here. A gross win, observed fill subtotal, zero subtotal, in-window win,
current flat state, first local row or caller genesis assertion cannot seed or
reset streak. UTC daily/7d full loss windows therefore remain absent.

The latest tail is not complete because a generation window passed B3's maturity
policy or two pages agree. Continuous equity peaks are not reconstructed from
closed gross outcomes. Old HWM samples without their contemporaneous native-clock
proof remain unknown; new current native source proof cannot upgrade them or
replace their genesis. Every diagnostic preserves:

```text
net_loss_window=None
loss_streak_at_history_start=None
historical_native_hwm_verified=False
current_tail_complete=False
source_authenticity_verified=False
owner=None
snapshot=None
account_revision_published=False
account_complete=False
execution_authority=False
admission=DENY
```

## Preregistered engineering scenarios

Each source-mutation test first replays a genuine positive synthetic component
baseline through the original collector and verifies its gross/subtotal. Inputs
are declared before acquisition; captured original bytes are never amended.
Negative ingress tests assert fixed causes rather than a generic exception.
The below cases describe implemented mechanics and the remaining original
requirement separately. Their runtime status is **NOT RUN by the source author**.

| Scenario | Implemented original-source mechanic | Full requirement still absent |
| --- | --- | --- |
| A01 | Flat/open/close, reconciled gross and signed fill subtotal | Complete net funding/outcome |
| A02 | Genuine partial fill before UTC midnight, final fill after midnight | Full UTC daily losses |
| A03 | Open entry fee retained at fill time, no closed outcome | Full current financial tail |
| A04 | Actual seven-day lower bound; original pre-window opening retained from an earlier capture; skipped measured interval denies | Continuous complete 7d history/loss |
| A05 | Gross win with negative observed fill-plus-fee subtotal | Complete net classification |
| A06 | Positive signed rebate and original bill mirror counted once | Full net funding proof |
| A07 | Original funding bill retained, effective accrual absent | Settled-event/held-inventory join **UNFULFILLED** |
| A08 | Original empty bills retained, funding still unknown | Complete no-applicable-funding proof **UNFULFILLED** |
| A09 | Later original capture discovers delayed funding bill; earlier net never becomes complete | Funding/finality invalidation of a complete projection |
| A10 | Bill generation is retained, supplied accrual keyword rejected | Genuine effective funding source |
| A11 | Bill generation at a fill instant still grants no funding attribution | Settled-event ambiguity/wrong-event attribution proof |
| A12 | Existing structural close before chosen history start, reset remains false | Positive complete-net reset/suffix **UNFULFILLED** |
| A13 | Exact observed zero subtotal, seed/reset absent | Complete zero-net classification |
| A14 | In-window win/current flat cannot establish starting seed | Original full seed proof |
| A15 | Caller genesis override rejected | Genuine genesis witness **UNFULFILLED** |
| A16 | Missing opening causes over-close denial | Source-proven pre-window inventory |
| A17 | Equal-time mixed roles remain ambiguous | Source-proven event ordering |
| A18 | Over-close does not produce a structural close | Complete supported lifecycle segmentation |
| A19 | Overlapping BTC/ETH source structures; no strategy-local or account-wide seed | Complete account-wide net outcome order |
| A20 | Foreign fee currency/unclassified account movement blocks totals | Complete supported product/movement coverage |
| A21 | Repeated identical source row deduplicates; changed original response keeps conflicting variants | Reconciliation/finality |
| A22 | Measured gap retains original finding and blocks total | Complete generation/observation continuity |
| A23 | Current tail beyond original mature cutoff remains unknown | Current UTC/7d financial completeness |
| A24 | Same original pins reproduce bytes; separate exact-ingress countercases deny | Native authenticity/owner/current full risk |

The suite also preregisters 12 exact-ingress countercases: boolean sequence,
missing prefix, changed head/source pin, foreign source/index class, altered
original receipt row time, boolean membership/witness index, caller funding DTO,
completeness and streak overrides. A pure arithmetic case verifies a valid
baseline before rejecting foreign scalar/time types. The static planned inventory
is 24 + 12 + 1 = 37 pytest nodes; collection/execution belongs to root and must
be recorded against the exact frozen source, not inferred as passing.

The old whole-byte compatibility corpus has 16 positive and six exact old error
cases, captured by root before this adapter change. Root must capture the new
818-file freeze separately and compare complete canonical members, then execute
the new cases and appropriate regression. An AST/scope check and Ruff result do
not replace that runtime comparison, PostgreSQL, native account acceptance or
full CTCC acceptance. No existing accepted receipt or failed artifact is replaced.
