# DB0056 Demo stopped Control setup

Use this command after setting up the reviewed restricted DB0056 source profile
and before collecting CURRENT or history for an account with no Control lineage.
Run it explicitly from the configured source runner.

```text
python -B -m scripts.initialize_demo_stopped_control_0056 --instrument BTC-USDT-SWAP --initialize-stopped
```

The signed Demo account selector supplies the exact UID. The operation takes the
account lock and creates a revision-1 stop with no owner and no Arm. The Control
row and its original stop journal entry are committed together under the existing
restricted writer role. A fresh transaction reads and verifies the committed
journal. If the lineage is exactly this command's stopped genesis, rerunning
reports the existing revision and does not write. Any other occupied lineage
causes a refusal.

If the command reports an uncertain outcome or is interrupted, use the
readback-only operation to reconcile before retrying initialization.

```text
python -B -m scripts.initialize_demo_stopped_control_0056 --instrument BTC-USDT-SWAP --reconcile-only
```

The startup, account observation and order routes do not invoke these commands.
Both options require the restricted, execution-disabled Demo profile and use only
the signed account-configuration GET at the exchange. Successful output has
`admission=DENY`, `armed=false` and `execution_authority=false`. It contains the
Control event digest, without account identity or credential values.

## Recover a previously initialized account

After a process interruption, explicitly latch a stop on the signed Demo
account under the same restricted profile.

```text
python -B -m scripts.recover_demo_account_restart_0056 --instrument BTC-USDT-SWAP --latch-stop
```

This operation clears a pending Arm and preserves the owner epoch, source
pins and existing journal. An already stopped account is read back without
another write. It refuses an absent or malformed lineage. The command opens
no exchange order path. Its only exchange request is the signed identity GET.
Position, intent and protection reconciliation still have to complete before
an execution path can admit the account.

Exit `2` marks an uncertain commit or an interrupted operation. Re-run with
`--reconcile-only` to verify the current account stop without creating a new
event. The readback alone cannot attribute a stop to a lost commit response.

With the stop in place, record fresh position, ordinary order and eight
separately paginated algo order observations.

```text
python -B -m scripts.observe_demo_current_source_0056 --instrument BTC-USDT-SWAP --restart-inventory
```

The command reopens the stopped Control lineage and local exposure checkpoint
in restricted transactions on both sides of the new exchange reads. A changed
local checkpoint or Control revision refuses the report. The usual source
capture initialization can retain a missing local scope at revision zero,
which stays unknown. The private receipt records row counts by query and
fingerprints the position rows. `OBSERVED_FULL_BRACKET_UNATTRIBUTED` adds the
fingerprint of a unique live exchange OCO row with matching instrument, margin,
closing side, exact contract size and mark-based TP/SL. Missing, overlapping
or conflicting evidence stays `UNKNOWN`. The receipt includes the number of
position rows omitted from the bounded detail list. Verified protection still
requires the durable intent and fill lineage. Restart inventory leaves the
stop latched and denies flat-start and execution permission.

## Check the configured Demo connection

Run `python -B -m scripts.check_okx_demo_connection` with the configured
Demo credentials and zero read retries. The command sends one signed
account-configuration GET and prints a status without the account identifiers.
`IP_NOT_WHITELISTED` indicates OKX code 50110 for the current connection.
An observed configuration leaves full account collection and trade admission
as separate checks.

## Compare CURRENT source and the stopped qualification ledger

The account must already have stopped Control and an initialized capture
scope. The qualification preflight uses a single pinned Demo read lease. It
compares two independently opened, UID-locked PostgreSQL observations around
the original CURRENT requests, including the Control event, account and ledger
revisions, currency scopes and unresolved reservations. The receipt binds the
durably replayed CURRENT page chain and the exact local ledger fingerprint.
Changed endpoint state or unproven chronology prevents receipt publication.

    python -B -m scripts.observe_demo_current_source_0056 --instrument BTC-USDT-SWAP --qualification-preflight

The UID lock is released before the requests and acquired again for the
post-capture read. The receipt records the two observed endpoints. It cannot
authorize an account revision, qualify a trade, or grant order permission.

Add `--key-policy` to show two redacted classifications on a successful
read. The reviewed key policy requires `EXACT_READ_TRADE` and
`SINGLE_PERMITTED_IPV4`. The connection check's exit code continues
to report whether signed configuration was observed. The two policy
classifications describe the permissions and IP field returned by OKX.
