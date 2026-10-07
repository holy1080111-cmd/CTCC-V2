# Account funding bill candidate audit (V1)

`account_funding_bill_audit.audit_funding_bills` is a read-only, additive
diagnostic over externally pinned, original Global Demo V5 B1 readbacks. It
replays the existing full page/history verifier. It does not alter the older
account lifecycle receipt or create a `PortfolioRiskSnapshot`.
`verify_funding_bill_audit` repeats that source replay and requires the supplied
receipt bytes to match exactly. A changed classification or locator is refused
even when the altered receipt still says `DENY`.

The [OKX Trading Account bill specification](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-bills-details-last-3-months)
defines account bill `type=8` as funding fee, `subType=173` as expense and
`subType=174` as income. For a funding expense, OKX points to `pnl` for the
payment. These definitions apply to `/api/v5/account/bills` and
`/api/v5/account/bills-archive`; the [Asset bills API](https://www.okx.com/docs-v5/en/#funding-rest-api-asset-bills-details)
uses different subtype meanings. The audit enforces the account endpoint and
requires the subtype, settlement currency, explicit `instType=SWAP`, SWAP
instrument ID and `pnl` sign to
agree before labeling a row a **payment candidate**. It retains original
page/row hashes and locators so the private source can be read back.

Account bill `ts` is a balance update or record-generation time, never a
measured funding accrual event. A fully paginated, terminal-empty account bill
chain proves only the bounded source query. The receipt leaves
`effective_accrual_at`, `funding_accrual_at` and `net_funding_cashflow` null,
and sets account completeness, authenticity and execution authority false with
`admission=DENY`. Empty account bill pages are **not** evidence of zero
funding, complete historical liabilities, or an execution-time loss window.

Future accrual proof needs independently observed settled event timing and a
reconciliation of authenticated account payments, positions, and full retained
history. It must be versioned; this audit cannot be reinterpreted into that
proof. The object construction fence limits normal Python callers only and its
hashes, including a successful byte-for-byte replay comparison, do not
establish exchange authenticity.
