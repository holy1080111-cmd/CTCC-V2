# Original protective exit: public DB0058 acceptance

`tests/integration/test_native_original_oco_public_0058_pg.py` adds seven
engineering cases for the actual isolated TLS DB0058 lab. Collection alone
does not pass these cases. The account, qualification and exchange documents
are declared synthetic test ingress; this is not an OKX acceptance result.

The fixture uses the migrated `public` schema, the restricted three-role
44-grant Demo profile, the retained B1 source chain, collector lineage and
issuer CAS, original bound reservation and intent, and immutable submission
outcome rows. It does not disable, replace or bypass a production SQL trigger,
mint a native source capability, run an operator Arm or call an exchange.
The before/after user-trigger catalogue must be identical.

The four positive cases cover long/short and net/long-short position modes:

1. Persist the original full entry and its exact active mark-price TP/SL.
2. Persist the protected hold and disarm the entry Control, retaining risk.
3. Append fresh synthetic original-OCO details, a unique fully reducing child,
   complete entry/exit fills, whole-account flat reads and bill page chains.
4. Execute the installed original-OCO SQL predicate and reservation trigger,
   then commit the exact revision-4 terminal transition.
5. Start a distinct read envelope after that commit, retain its new raw pages
   through the installed submission-binding trigger and independently reopen
   the full canonical hash chain with the production DATA decoder. The original
   closure timestamp, intent, event and single hold/closure identities remain.
   An attempted raw row with a different closure link must be rejected by the
   actual submission-binding trigger before the valid journal is appended.

The three negative cases change protection, report a partial child fill or
report ambiguous children. Rehashed assessment JSON cannot pass the installed
predicate. A real nested update is rejected by the actual reservation trigger;
a different committed session must still observe revision-3 uncertainty,
no terminal transition and disarmed Control.

Both paths execute the actual native local-journal SELECT and strict DATA
projection on DB0058, including an independently scoped empty result for an
unrelated synthetic UID. This narrow SQL/projection check does not replace a
signed identity selector or claim the full HTTP service executed. Protection
remains explicitly `NOT_RECHECKED` in this local listing.

`tests/unit/test_public_protective_0058_fixture_v1.py` separately validates the
four predeclared fixture variants and the writer-serializer/production-reader
raw-byte roundtrip. Its in-memory sink proves no database or venue acceptance.
The existing TEMP-table binding-component cases remain separate, narrower
checks and do not substitute for these public-schema cases.
