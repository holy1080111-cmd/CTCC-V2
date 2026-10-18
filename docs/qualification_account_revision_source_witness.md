# Demo account revision source witness (DB0043)

DB0043 records a DENY-only binding from one existing Demo qualification ledger
revision to immutable B1 historical/current capture terminals, a B3 typed
historical index, and a DB0038 current observation index. The repository
replays each saved index from its original raw B1 chain before writing. It then
holds the exact account UID advisory/row lock, compares the saved claims hash
and revision, copies the exact claims bytes into the immutable witness row,
verifies the V5 historical and V7 current plans and their source heads,
commits the witness, and repeats the source and witness readback in a separate
database session. Older witnesses remain replayable after the mutable ledger
scope advances. One exact scope/revision permits at most one row.
The decoded claims scope, account UID/currency, authority environment and all
five account-stamp UIDs must match the locked scope. The insertion trigger
checks the same nested identities from stored claims JSON even when an insert
bypasses the Python repository.

The caller can supply only the scope, expected account revision and two index
sequence numbers. It cannot supply claims, source bytes, a region verdict,
completeness or execution flags. The database independently checks source
references, claims and document hashes. It forbids update, delete and truncate,
and every persisted/readback verdict is `DENY` with all authority flags false.
A failed or uncertain readback leaves the committed row intact and returns no
usable result; retry cannot overwrite it. Downgrade refuses a populated table.

This is a forensic prerequisite, **not** a collector-owned account-revision
issuer. The ledger revision still originates in `reconcile_scope` caller
claims. The original V5/V7 captures and B3/DB0038 indexes remain incomplete
and their registration-region/session pins are assertions rather than verified
credential/first-party identity proof. The current sources do not establish a
complete historical cashflow/loss seed, native high-water mark, funding
accrual semantics, local in-flight/uncertain inventory, protection coverage or
a complete `PortfolioRiskSnapshot`. The witness does not grant G1–G12, R7,
reservation, intent or order authority. In particular, the existing ledger
`_require_owned_account_revision` and post-G12 R7 denial stay unchanged.

A positive issuer requires a separate reviewed contract that authenticates the
registration site and controlled credential session, proves every historical
and current account page with causal timing, materializes the complete
portfolio from those exact rows plus local durable state, and binds the
result to an issuer-owned revision under the same UID lock. It must then
read back that exact revision in another session before any reservation use.
This DB0043 witness cannot be promoted by changing its flags or by inferring
completeness from a successful insert.

The separate [recorded source claim](qualification_account_revision_recorded_claim.md)
can now replay a witness's original current B1 chain and compare exact
settlement balance amounts with the stored caller claims. That read-only
projection stays DENY and does not issue or authenticate the ledger revision.

The isolated PostgreSQL tests cover exact-source binding without promotion,
wrong revision/source rejection, two competing writers, durable denial after
a late readback failure, old-revision replay after ledger advancement, direct
SQL update/delete rejection and six nested-identity SQL forgery cases. The
module is registered in the Linux PostgreSQL shard and Windows PostgreSQL
classifier. These cases use
synthetic read-only account packets; they are not authenticated Demo account
or Live acceptance. PostgreSQL results must be recorded against the exact
integrated source before release.
