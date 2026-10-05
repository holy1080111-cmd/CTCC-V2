# Executable quote v2 precursor and fixed diagnostic profile

This additive contract is not execution eligibility. `ExecutableQuoteV2` carries
typed REST ticker/mark observations and an exact replayed `FundingObservationV2`.
`inspect_executable_quote_v2` recomputes a fixed, versioned CTCC policy and returns
canonical diagnostic bytes. Even `profile_satisfied=true` has `admission=DENY`,
no source/PIT verification, no account completeness and no execution authority.
The record's caller-supplied times and ticker/mark digests are not native proof.
There is no source owner, network call, callback, wall-clock lookup or order path.

## Preregistered local policy and reasons

`POLICY_BYTES` and `POLICY_SHA256` bind
`ctcc.executable_quote_diagnostic_policy.v2`. The profile is fixed in source and
is not a caller-selectable collection of thresholds. It supports global public
analysis or Demo SWAP observations only. Its boundaries are defined before new
source capture and still require the scheduled behavioral tests and review.

| Quantity | Fixed boundary | Reason and limitation |
|---|---|---|
| Ticker source generation age | at most 5 seconds | Preserve the existing fast executable-price requirement. |
| Mark exchange-return age | at most 5 seconds | Preserve the existing conservative mark requirement. Quiet-market rejection is possible. |
| Each component receipt/body age and capture completion age | at most 5 seconds | An earlier funding publication does not permit stale measured reception. |
| Funding exchange-return age | at most 90 seconds | Funding has a distinct documented WS publication cadence; this is a CTCC diagnostic age choice, not a REST cache SLA. |
| Future timestamps | zero tolerance | Every source must precede its own receipt; every receipt must precede completed capture and validation. |
| Funding processing state | reject | A processing settlement cannot support a new-exposure diagnostic. |
| Upcoming settlement reached | reject at equality or later | Never apply the previous forecast to a crossed settlement. |
| Upcoming settlement transition guard | reject at 95 seconds or nearer | Conservative local exclusion: a 90-second funding phase plus a five-second fast phase. It is not a claimed settlement duration. |

The official [funding WS reference](https://app.okx.com/docs-v5/en/#public-data-websocket-funding-rate-channel)
documents 30–90 second pushes. The [REST funding reference](https://my.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate)
pairs the forecast with `fundingTime`; `nextFundingTime` belongs to the following
forecast period. Neither provides proof of first rate-generation availability
or a guarantee that a retained REST response is the latest generation. Both
quantities remain explicitly unverified. Parser `ts` is never replaced with
receipt time. Forecast and raw payload hashes remain tied to the actual upcoming
settlement, including when following-settlement time appears more convenient.

Five- and ninety-second age boundaries are inclusive; the 95-second settlement
guard is exclusive for satisfaction. Time arithmetic uses exact timedeltas.
Funding source time may precede acquisition because exchange publication can
precede a new read; all acquisition/receipt/completion ordering remains checked.
The profile does not extend candidate expiry or reduce the required future
timestamp checks. No field labelled `passed` is accepted as an input.

## One consistent future G1/recheck path

This precursor deliberately does not accept a v1 quote or a diagnostic receipt
as source authority. A future additive path must establish the following joins
before any diagnostic can participate in qualification:

1. The private invocation owner captures genuine raw sources, retains the
   native clock and HTTP/WS chain, and constructs the v2 observations internally.
   It checks the ticker/mark raw values against the named digests and retains
   independent WS quote comparison, candles, books, OI and instrument metadata.
   It selects the fixed profile before requests, not in response to a failure.
2. Initial source-derived G1–G11 consume the same validated market context and
   preregistered strategy, cost and risk policy. A typed inspector result is
   diagnostic only; the owner preserves its source membership and invocation.
3. Candidate/event/zone/entry/SL/TP/policy are frozen before the real fixed-version
   G12 publisher. The resulting artifact/readback barrier is retained privately.
4. The same invocation starts **all new** public/account requests after that
   barrier, creates another v2 context from those raw sources, and reruns current
   G1–G4 and original-event survival using the original candidate. The inspector
   does not accept a caller assertion that its capture followed a real barrier.
5. Recheck then uses the same existing structural/location/cost/risk arithmetic,
   fixed plan and worst admissible economics. Atomic reservation, durable intent,
   independent commit readback and native final submit fence remain subsequent
   independent requirements. Missing account or authority dependencies stay DENY.

To avoid a parallel trading engine, extract shared **pure arithmetic operands**
from current helpers only when integrating the new route, then prove the v1
adapters produce identical old results/bytes. Keep the existing v1 entry points
and defaults unchanged. Add thin v2 adapters that obtain validated operands from
the owned v2 context; do not convert it to a v1 DTO with fabricated funding time,
increase the common v1 age, monkeypatch callbacks, or rerun a failed original
candidate under a more permissive profile.

The precise review surface for that integration is:

| Existing area | Required explicit v2 join |
|---|---|
| `public_source_runtime`, `qualification_runtime`, post-G12 public runtime/journal | Fixed profile and exact raw/component membership inside the existing private scopes; true publication barrier. |
| `market_bridge`, qualification `data` | Versioned market context and explicit upcoming/following funding times; no aliasing old `next_funding_time`. |
| `location`, `fixed_protection` | Same structural checks and fixed original geometry with the new inspector's separately validated price operands. |
| `economics`, `executable_economics` | Same candidate and executable-reference arithmetic; versioned funding holding assumptions and no favorable-rate rescue. |
| `service`, `history_prefix`, `current_conditions` | Same strategy/history candidate lineage and signed-funding checks from the owned context. |
| `trade_evidence/service`, publisher | Actual new schema/profile identity participates in original publication and immutable readback. |
| Qualification ledger, reservation and intent binding | Bind the new original/recheck digests explicitly; no acceptance through legacy envelopes. |

No file in that integration table is changed by this precursor. Existing v1
inspectors reject the new type. MIE's frozen offline proration model and prior
reports are not silently changed into realized funding calculations.

## Verification limits

Authored synthetic tests target exact age/transition boundaries, wrong clocks,
different instruments, crossed prices, invalid source types, timestamp rescue,
typed flag forgery, deterministic replay and old-inspector rejection. Tests are
not executed during the host's thermal-recovery restriction; root schedules a
bounded run. Short lint/format validation does not prove behavioral acceptance.
No new native sample, G12 example, Demo trade or Live authorization is produced.
