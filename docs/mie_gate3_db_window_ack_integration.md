# Gate 3 complete-window PostgreSQL join acceptance

`tests/integration/test_gate3_blind_window_schedule_ack_binding_repository.py`
exercises the existing V2 complete-window binding against real, disposable
PostgreSQL `0026` schedule and `0028` acknowledgement rows. It uses the
restricted direct-login roles provisioned by the isolated `0028` test fixture,
publishes the exact canonical schedule before a synthetic future window,
confirms that a pin without an ACK is insufficient, then obtains the separate
ACK and replays all four synthetic minute rows from their original journal
bytes. A fresh pair of restricted connection pools verifies the frozen binding
against the immutable database records. Missing journal coverage and a changed
schedule identity fail closed.

Run the focused test with an explicitly isolated `DATABASE_URL` whose account
can create and remove disposable databases and login roles. Without that URL,
pytest reports a **skip**, not a pass. This test applies `0024`, `0026`, and
`0028` directly; it does not replace the exact-source full Alembic migration,
schema-drift, or production role audit.

The source packets and window are synthetic. The ACK proves only that the
canonical pin was committed and visible to a separate restricted login before
the window **according to that PostgreSQL server clock**. It does not establish
trusted UTC, independently protected WAL/backups, real in-window market
acquisition, independent first evaluator access, point-in-time historical
availability, or a predictive OOS result. The frozen contract keeps
`trusted_clock_verified=false`, `independently_protected=false`,
`evaluator_first_read_proven=false`, `predictive_oos_eligible=false`,
`promotion_eligible=false`, and `execution_authority=false`. Gate 3 remains
closed until those missing provenance and evaluation requirements have their
own evidence.
