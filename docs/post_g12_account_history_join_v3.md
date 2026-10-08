# Post-G12 V7 account-history join diagnostic

`publish_capture_public_account_history_v3` is a separate DENY-only path. It
leaves the V2 API and receipt bytes unchanged. It accepts the exact current
V7 Demo account session and a V5 history capture ID as a database locator.
The ID supplies no history claim: the native account runtime rereads and
replays the committed V5 chain before its first HTTP request, then captures
V7 current account data and checks both under the exact UID lock. The V5 and
V7 plans have different hashes; their exact UID, main UID, session binding,
region, origin and local checkpoint must agree inside that runtime.

The enclosing invocation first replays the original caller-provided G1–G11
run, publishes and reads back a new G12, then collects fresh public data. It
checks every public request start after the publication barrier. Only then
does it invoke the native V7 account-history join. The V3 receipt pins both
capture references and the native join, checks that the committed-history
readback and replay finish before the account session's first HTTP request,
and checks that request starts after G12. That first HTTP request is the
account session's public time probe, not the first private account GET.
The native runtime itself orders the current private requests after the probe.

The same invocation reopens the public journal, recomputes the fixed original
candidate's public-only G1–G4, event/zone and projected economics, and
publishes a separate no-clobber recheck receipt. The V3 join receipt is also
no-clobber and separately reopened. A late readback failure leaves already
written bytes intact and fails the invocation. An unfinished account or
recheck path does not publish partial account pins as complete.

This remains an engineering diagnostic. Its original G1–G11 run is still
caller-origin. The native join reports an unclosed history tail and other
account gaps, so `account_complete=false`, `execution_authority=false`, and
all reservation, intent and order flags remain false. The native Demo public
origin still lacks authenticated registration-region evidence and refuses
production capture before G12; positive tests use explicitly synthetic
transports and source receipts. No Demo or Live order can follow from V3.
