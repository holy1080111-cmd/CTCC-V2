# Same-frame original bracket and controlled journal inspection

`inspect_owned_original_bracket_journal_v12` composes the V11 fixed structural
bracket inspection and V10 controlled event-journal observation in one private,
one-use original-source capture. The original public and account packet objects,
precursor, G5 event key, report, entry and source policy stay fixed across the
two locked journal reads. The owner replays G5 and the original bracket after
those reads and checks the source lifetime. Both nested canonical receipts,
their hashes and shared lineage are validated before returning a typed V12
receipt. A caller cannot supply a historical V10 or V11 receipt for joining.

The V12 status distinguishes source refusal, absent precursor, unavailable or
changed ledger, consumed event, unavailable bracket and inspected-but-still-
unqualified geometry. A controlled-journal absence proves only absence in that
bounded local journal. It does not authenticate the complete historical event
epoch or exchange orders. A failed DB read, changed event, expired source or
inconsistent nested record cannot become a passing candidate.
The portable V12 receipt and its hashes can also be assembled from supplied
synthetic records; a saved receipt alone does not prove the owner ran or that
the two observations share authentic custody. Later gates must verify their
own source and journal authority rather than promote this diagnostic.

Every V12 receipt has `admission=DENY`, `execution_authority=false` and all
historical availability, G6–G9, G12, account completeness, reservation, intent
and order flags false. The original bracket is an uncalibrated observation, not
a certified G8/G9 result. Region provenance is still unproved for the real Demo
account, so that source can be denied before any I/O. Synthetic tests prove
same-frame custody and refusal rules only; they are not Demo acceptance.
