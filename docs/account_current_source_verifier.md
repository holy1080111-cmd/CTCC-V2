# Current account source observation (B4)

B4 verifies the original externally pinned B1 journal and replays its full
packet/page chain through the existing B2a verifier. It records the actual
current endpoint observations: exact config UID/mainUID/modes, settlement
balance, positions, ordinary pending, the four algo order types pinned when
the v5/v6 plans were sealed (`conditional`, `oco`, `trigger`,
`move_order_stop`), instrument
metadata and leverage responses. The v2 policy hash binds this exact stream
inventory and the v5 capture-plan contract. Its versioned policy supports
global Demo, account level 2, USDT and the captured SWAP product semantics.
Other observed products remain in source records and deny this supported scope.
v3 SWAP, v4 standard-product and v5 current capture scopes remain named
separately. The v2 current-source verifier accepts only the exact v5 plan;
historical v2/v3/v4 packets cannot provide current-source observations or flat
diagnostics. Current inventory requests must be unfiltered and complete under
the existing endpoint-specific terminal/page rules.

The four pinned `ordType` values no longer cover the current regional
[OKX algo order list contract](https://my.okx.com/docs-v5/en/#trading-account-rest-api-get-algo-order-list),
which also lists `chase`, `iceberg`, `twap`, and `smart_iceberg` with
`algoId` pagination. The original v5/v6 plan and packet bytes remain
replayable, but their empty four-query subset now yields
`algo_type_coverage_incomplete`, `observed_flat=false`, and `admission=DENY`.
New observation receipts use schema `ctcc.current_account_source_observation.v5`
and bind an explicit `ctcc.current_algo_coverage_revocation.v1` digest into
`effective_policy_sha256`. The earlier policy hashes and saved source receipts
are unchanged; their earlier `observed_flat=true` diagnostic is revoked for
current use. No earlier receipt gains current flat-start qualification. A new versioned
current capture plan must pin the account's actual registration region and
query every applicable documented algo type through an empty terminal page;
this verifier does not infer missing types to be empty.
Ordinary pending orders use the exchange's [order list
endpoint](https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-order-list)
and its documented `ordId` pagination; cursor identity is not mixed with
`algoId` or bill/trade identifiers. An unsupported or undocumented algo type
cannot be silently added to the current inventory contract.

The separate `ctcc.demo_current_account_plan.v7` and
`ctcc.current_account_source_policy.v5` pin eight algo `ordType` chains in the
regional documented order: `conditional`, `oco`, `chase`, `trigger`,
`move_order_stop`, `iceberg`, `twap`, `smart_iceberg`. The V7 pure verifier
requires the original complete B1 journal, exact plan/session/scope binding,
replayed `body_complete` EOF witnesses, unfiltered inventory queries and an
empty terminal page for **each** algo chain. Missing pages, stale receipt,
cursor conflict or wrong policy/packet version fail closed. Its receipt is
`ctcc.current_account_source_observation.v6`, with the eight terminal receipt
hashes and a distinct policy SHA. An all-empty supported inventory can yield
`observed_flat=true` only as a bounded current-source diagnostic; the receipt
still has `flat_start_permission=false`, `account_complete=false`,
`execution_authority=false`, and `admission=DENY`. V5/V6 packet bytes and
policy hashes remain unchanged and their current-use flat assertion remains
revoked. V7 does not by itself prove registration origin, native ownership,
account-wide atomicity, local liabilities, protection, or complete history.

The measured balance stamp retains request start, headers, body completion and
original receipt identity. Original account/detail `uTime` values remain
separate last-exchange-change times. An old unchanged `uTime` is not replaced by
now; a new measured receipt does not prove that an exchange value changed
recently. Settlement `eq` and `availEq` are read from the exact currency detail;
top-level USD values never substitute for missing USDT values. Missing operands,
liability fields or anchor contradictions remain incomplete.

For pinned Futures account level `2`, OKX marks top-level balance `availEq`
and account-position-risk `adjEq` inapplicable while balance
`details[].availEq` applies to Futures mode. The capture packet permits absent
or empty top-level values only for that verified mode and requires the exact
settlement-currency detail's available equity. Raw empty fields remain in the
receipt. The current-source path supports only level `2`; the mode `3`/`4`
capture checks still require their applicable top-level values, while level
`1` remains conservatively incomplete for this SWAP path. The current-source
receipt lists the original packet's incomplete reasons and denies
`observed_flat` when a *current* source field is missing. A history-only gap
remains visible but does not change the narrower current-inventory diagnostic.
Neither condition grants account or execution authority. [OKX account balance
and risk fields](https://www.okx.com/docs-v5/en/).

The policy fixes a 120-second maximum current-response span and a 30-second
maximum age at the explicit validation cutoff. For the active V6 v4 policy,
the age starts at the earliest original B1 `body_complete` event: the measured
end of response bytes, replayed against the page and journal. The packet's
`body_completed_at` is the later response-close upper bound and cannot make
an old page fresh. V6 v3 receipts retain their original policy hash and
response-close interpretation for historical replay; they do not acquire v4
qualification. Missing or inconsistent EOF evidence fails closed. These are
measurement-policy bounds, not exchange-global atomicity.
Both V6 replay policies also require the packet plan's Demo environment, UID
and settlement currency to equal the requested ledger scope. Matching only the
rechained journal start fields cannot transfer one account's empty-inventory
observation to another account. This check grants no source authenticity.
The pure verifier's declared `validated_at` is not a live clock capability. A
future owned coordinator must obtain its actual current clock and credential
session again and reject stale replay. No captured update time is altered.

`observed_flat` is a diagnostic: it requires actually empty positions, ordinary
pending and all applicable algo rows, complete original current page chains, supported
identity/mode/currency and no checked contradiction. It never means flat-start
permission. Local reservations, unresolved intents, uncertain/untracked
exposure, exact active TP/SL, source authenticity and current owned session are
unjoined. All outputs retain `flat_start_permission=False`,
`account_complete=False`, `execution_authority=False`, `admission=DENY`.
Nonempty exposure is preserved and denied, without cancellation, closure,
reduction inference or protection inference from an attached request.

This slice does not invent funding accrual, full lifecycle results, loss-streak
seeds, measured HWM or local/exchange revisions. It has no authenticated IO,
order route, storage migration or authority publisher. Existing DB0020 contains
the exact durable raw sources needed to reproduce it. Its detailed receipt is
private account evidence and is not an automatic public report or Notion payload.

The authored tests cover empty-vs-missing current pages, all pending pages,
unchanged old update times, receipt staleness, missing settlement equity,
Futures-mode field applicability, packet source-field denial, anchor
contradiction, nonempty exposure, non-SWAP retention, wrong pins/scope,
replayed receipt input and preserved B1 bytes. They are synthetic and do not
constitute native account acceptance.
