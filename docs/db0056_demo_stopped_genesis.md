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
