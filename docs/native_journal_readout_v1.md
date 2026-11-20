# Find a native Demo operation after a lost response

On the manual Demo dashboard, enter the local CTCC operator token and choose
**查找本機交易紀錄**. Select the recorded operation to copy its reservation ID
into the reconciliation field, then choose **對帳此預留紀錄**. These actions do
not repeat an entry, Arm the account, cancel protection or release a risk hold.

The authenticated `GET /api/demo-automation/native/journal?limit=100` first uses
one original configured signed Demo account/config GET. The existing selector
pins the exact UID/session, actual physical database head and three restricted
roles. One bounded scoped SQL statement then reads local reservations. Unknown
identity, unreadable data or an unexpected catalog returns a refusal, never an
empty successful list. No owner database connection or new grants are used.

Native reservations are listed with unresolved records first. The response also
counts unresolved older-contract records in the same exact account/currency;
they cannot disappear just because the native list is empty. Truncation is
explicit. The API exposes neither raw UID nor credential/session values.

This is a local journal, not a fresh position/protection reconciliation. Every
row therefore says `protection_status=NOT_RECHECKED`; account completeness and
all execution/retry authority fields remain false. Even a locally empty list
does not establish exchange flatness. Entry continues to require the original
complete account source, qualification, fresh recheck, reservation and intent.

The dashboard keeps new entry/configuration disabled while discovered unresolved
work remains. A failed/malformed journal response records an unknown blocking
state. Reading ordinary status cannot clear that state. A new valid journal
read is explicit; existing ambiguous-submission and protected-position latches
keep their original rules. Stop and Emergency Stop remain available.

Validation is scoped: 37 local DATA/HTTP/UI contract tests and nine executable
synthetic DOM scenarios passed without database or exchange requests. The local
SQL query still requires the final isolated PostgreSQL integration run; these
tests do not substitute for provider acceptance or the deferred real examples.
