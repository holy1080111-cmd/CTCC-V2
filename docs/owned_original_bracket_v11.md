# Same-frame original entry and structural bracket inspection

`inspect_owned_original_bracket_v11` is a read-only diagnostic for the four
base strategies. Its source owner captures the public and Demo account packets
once, derives the original precursor and G5 event, then replays those same raw
packets to inspect a fixed entry, stop and target. The versioned policy is
explicitly an uncalibrated inspection scenario. The returned typed receipt
contains bounded hashes and prices, never the account packet or credentials.

A missing valid bracket leaves the stop and target unknown. A changed source,
event, original entry, policy or expired event is rejected; the owner does not
move the entry, stop or target to make a trade pass. V10 receipts remain
byte-identical under the regression fixture. The source may be denied before
I/O while Demo registration-region provenance remains unproved.

Every V11 result has `admission=DENY` and `execution_authority=false`.
Historical first availability, authenticated event history, G6–G9, calibrated
economics, complete account risk, G12, reservation, intent and execution are
not established. In particular, a geometrically available bracket is not a
qualified candidate and cannot authorize an order. Standalone V10 and V11
diagnostics use separate one-use account sessions; their receipts must not be
joined after the fact. The separate V12 entry point inspects both in one frame.
