# Local manual Live tool

`app.api.native_live_manual_v1:app` is a dedicated manual tool, separate from
the legacy application and Demo automation. Bind the server to127.0.0.1; the
application also rejects non-loopback/unknown peer addresses and unapproved
Host headers. There are no docs/OpenAPI/CORS or legacy order routes. Protected
actions require the existing safe CTCC token in `X-CTCC-Token`.

Opening the static page triggers no account collection, Arm or order. The token
is held only in the current page; it is not saved in cookies/browser storage or
placed in a URL. Responses disable caching, the page uses a per-response CSP
nonce, and private validation/configuration/provider data is not echoed.

Startup imports the genuine private runtime first. Missing configuration is
`NOT_CONFIGURED`, with no database/source call and positive actions returning503.
A configured startup performs only the durable negative stop/inhibit and its
independent readback. `STOPPED_DEFAULT_OFF` means that operation was confirmed;
it is not account-flat proof, qualification, Micro Live readiness or acceptance.
Unknown cleanup/readback remains blocked and is not translated into false/empty
account state.

Prepare starts one background worker. That worker opens and retains both the
private runtime and original source/session, publishes a redacted challenge,
then awaits the current human response in the SAME task. HTTP approval only
signals the nonce/phrases; it never receives an account source, factory, risk
result, candidate or execution capability. The optional exit selection requests
an additional distinct exact confirmation. It does not authorize an exit alone.
The source challenge must explicitly declare its separate controlled-exit
duration:120 seconds when requested,0 otherwise. Before approval the page says
the permission lasts120 seconds after confirmation and permits only one close.
The UI refuses missing, changed or inferred durations; it does not mint the
permission or derive its deadline from the entry Arm.

The challenge shows the original trade scope, existing leverage≤3 and exact
caps. Source Decimal strings (including a bounded1E-20 representation) are
normalized for display only; finite positive bounds remain≤0.25/0.25/25/25 USDT.
Source/control/qualification operands are not rewritten.

The worker calls the genuine qualification once, then the separate source-owned
lifecycle before closing either context. A success view is published only after
the context's durable stop/cleanup completes. Fixed, source-defined pre-intent
rejections display `WAIT`, `CANCELLED` or `EXPIRED`; a confirmed stop before human
Arm displays `STOPPED_BEFORE_ARM`. Unknown errors, commit/readback uncertainty,
post-submit uncertainty and late cleanup failures display `UNCERTAIN` and retain
the core journal/holds. The tool does not infer global exchange flatness from a
rejection or a stopped worker.

Stop revokes the current source/session synchronously before cleanup awaits.
Journal read and explicit pending-intent reconciliation have separate actions;
they never enter the qualification/submit route. Missing/oversized/unknown
journals are errors, not an empty successful result. Each new prepare requires
a fresh setup check and a new human challenge; no scheduler or auto-Arm exists.

Validation on this UI slice:30 Windows tests passed with0 skips, including real
ASGI routes over explicitly inert core seams, current source Decimal examples,
remote-peer rejection, safe rejection/uncertain-cleanup classification, genuine
runtime/coordinator imports and missing-configuration default-OFF behavior. The
lifecycle dependency is still a required refusal when absent. No actual API,
credential, database, Arm, venue order or Micro Live acceptance was performed by
these tests. Gate3 and real-case acceptance remain deferred by project scope.
