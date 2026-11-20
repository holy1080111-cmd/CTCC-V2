# Notion source root identity and transaction leases

The issuer retains a private root/ancestor **identity pin**, not a publisher
lease. The original implementation retained `outbox._root_context`, which
acquires the exclusive publisher lease; a nested `outbox.read_job` then correctly
failed to acquire the same lease. That also prevented later independent claim
and dispatch journal transactions. Actual58 reporting exposed this in the
configured SQL→file→Notion-source engineering child.

`native_outbox_root_pin_v1` retains original directory handles for every
ancestor. POSIX opens with `O_NOFOLLOW`, compares device/inode/mode/owner on the
held descriptors and original parent/name chain at every replay. Windows opens
non-publisher directory handles with read/write sharing but no delete sharing,
pins volume/file identities, checks type/reparse state, and independently opens
the original paths again. A root or ancestor replacement, symlink/junction,
invalid private ACL, closed handle, missing directory or wrong type refuses.

Every existing outbox read, claim, dispatch and journal append still acquires
its own unchanged exclusive publisher lease. Concurrent publishers remain
excluded. The source checks its original identity pin on every guarded resume,
and retains genuine role/factory/source/settings/expiry checks plus two fresh,
independently committed SQL readbacks. No schema, grant, source type, trading
receipt, timeout, account or publication authority is changed.

Retirement closes identity handles and preserves accepted journals. A late
failure never deletes an accepted report/state file or retries an order. The
actual58 SQL child now additionally proves that an independently opened
publisher transaction works while the source pin remains active, and that a
second simultaneous publisher remains rejected. Its raw/destination fixtures
are explicitly synthetic; no Notion HTTP or exchange order is sent.
