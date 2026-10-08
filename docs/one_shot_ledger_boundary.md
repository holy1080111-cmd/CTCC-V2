# R7 one-shot → DB0017 event boundary (diagnostic)

`publish_capture_inspect_event_ledger` owns the existing G12 publication,
post-publication public and Demo account captures, and fixed-candidate recorded
recheck in one invocation. It then validates the G12/recheck/capture lineage and
reads the original event key through DB0017's account-scoped, locked
`read_event_observation`. Before that read, the bridge freezes the original
candidate inputs and independently replays the recorded R7 assessment from the
same original market, newly captured market/quote and verified account
materialization. A changed risk input or caller mutation after G12 is rejected.
The diagnostic pins the evidence, recheck and account packet hashes, exact
DB0017 reservation ID and measured event observation hash.
An existing reservation, including a terminal tombstone or one stored under
another settlement currency for the same UID, is reported as already recorded.

The read starts strictly after the one-shot result. A different account UID,
missing capture, changed source/candidate pin, mismatched event observation or
unavailable database fails closed. The result contains no raw account bytes,
credentials or SQL error. It is never an event-absence permit: a read with no
row does not prove external history completeness or exclude a later writer.

The current one-shot account materializer has no authenticated complete
PortfolioRiskSnapshot or independently verified local guard/history. Its
`RecordedRecheckAssessment` also records an unknown intrabar path. Therefore
every outcome remains `DENY`, with `atomic_risk_reserved=False`,
`durable_intent_created=False` and `execution_authority=False`. This module
calls neither `reserve_control_bound` nor
`consume_with_control_bound_submission_intent`, and it has no exchange client.
DB0017's reservation and intent contracts remain the destination only after a
future trusted account source, current risk replay, account-scoped lock and
independent readback are actually available; these requirements are not
satisfied by the diagnostic hash.

`tests/unit/test_one_shot_ledger_boundary.py` exercises both directions with
real synthetic G1–G12/recheck evaluators and raw simulated GET/WS capture,
then a fake read-only event observation. It checks absent, changed and failed
ledger reads, wrong UID before G12, forged risk assessment, caller mutation,
and immutable denial flags. These are
synthetic tests; they are not a PostgreSQL, Demo or execution acceptance.
