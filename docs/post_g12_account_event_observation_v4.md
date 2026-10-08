# Post-G12 account event observation V4

`publish_capture_inspect_history_event_v4` owns one new V3 G12/public/account
diagnostic invocation, reads its immutable receipt back, and then asks the
DB0017 qualification ledger for the exact original event key. The database
read starts after the V3 receipt's final observation and before the original
candidate deadline. It binds the same Demo account UID and settlement currency,
report, candidate geometry, history locator, G12/public evidence and V7
account-source references. A renamed report or event cannot turn an existing
event into a new opportunity.

The result distinguishes an already recorded event from an event absent at
the measured ledger read. Absence is only a local observation; it is not a
reservation or permission to submit. Invalid, missing, late or conflicting
source/ledger evidence produces a bounded rejection code without private
account, SQL or credential content. The receipt's code-specific pin checks
prevent an incomplete path from claiming a completed observation.

Every V4 result is `DENY`. The original G1–G11 run is still caller-origin,
the V5 history tail and PortfolioRiskSnapshot are incomplete, and the
post-G12 result is public-only. V4 neither reserves risk nor creates a durable
intent, Arm state or order. Demo and Live transport hard denial remains in
force. Synthetic unit tests exercise event/UID/deadline/receipt conflicts;
the isolated PostgreSQL integration case must run in matching CI before its
database behavior can be credited.
