# Live financial custody in main0058

The unreleased sole main0058 migration now installs the two append-only Live
financial tables after the separate Live control/intent ledger. It installs no
role, grant, control, Arm, sample or account data. All migrations through0057
remain byte-identical; the source pin still covers exactly59 migration files.

Upgrade verifies the installed financial guard body, owner, trigger signature,
configuration and both actual enabled triggers. Downgrade locks all retained
tables, refuses any saved financial prefix or Live control/intent, and refuses
non-owner table or function privileges before removing new objects. Financial
objects are removed before their Live control dependencies. No retained row is
deleted and no CASCADE is used.

The new models are registered with the existing schema-drift checker. The
financial PostgreSQL fixture now requires the actual migration-created tables;
it does not install a test-only substitute. The bounded isolated migration probe
requires all17 original Live ledger cases plus all11 exact financial cases,
including every parameterized legacy-exposure/latch case.

Local validation:32 migration/source/verifier tests passed, zero failures,
errors or skips. Eleven financial PostgreSQL cases were collected successfully.
Actual PostgreSQL, fresh upgrade/downgrade/re-upgrade and schema comparison must
be rerun on this exact source in the isolated hosted probe before acceptance.
