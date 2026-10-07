"""Offline safety/ordering checks; PostgreSQL trigger behavior is tested separately."""

from tests.durable_migration_fixtures import load_migration


class Recorder:
    def __init__(self):
        self.sql = []

    def execute(self, statement):
        self.sql.append(str(statement))


def commands(direction):
    module = load_migration("0027")
    recorder = Recorder()
    module.op = recorder
    getattr(module, direction)()
    return recorder.sql


def test_guard_upgrade_targets_public_under_nowait_lock():
    lock, audit, function = commands("upgrade")
    assert lock == (
        "LOCK TABLE public.qualification_reservations, "
        "public.qualification_reservation_transitions "
        "IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    assert "FROM public.qualification_reservations" in audit
    assert "WHERE state = 'reconciled_flat'" in audit
    assert "qualification_reservation_transitions" not in audit
    assert "post_submit_closure_legacy_terminal_unresolved" in audit
    assert "FUNCTION public.qualification_reservation_update()" in function
    assert "SET search_path = pg_catalog, pg_temp" in function
    assert "OLD.state = 'consumed' AND NEW.state = 'uncertain'" in function
    assert "OLD.state = 'uncertain' AND NEW.state = 'reconciled_flat'" not in function
    assert (
        "OLD.state = 'reserved' AND NEW.state IN ('consumed','uncertain','reconciled_flat')"
        in function
    )
    assert "NEW.state_revision <> OLD.state_revision + 1" in function
    assert "to_jsonb(NEW) - ARRAY['state','state_revision','updated_at']" in function


def test_guard_downgrade_requires_empty_public_ledger_before_restoring_old_function():
    lock, guard, function, reset = commands("downgrade")
    assert lock.endswith("IN ACCESS EXCLUSIVE MODE NOWAIT")
    assert "SELECT 1 FROM public.qualification_reservations" in guard
    assert "post_submit_closure_guard_downgrade_requires_empty" in guard
    assert "OLD.state = 'uncertain' AND NEW.state = 'reconciled_flat'" in function
    assert "SET search_path = pg_catalog, pg_temp" not in function
    assert reset == (
        "ALTER FUNCTION public.qualification_reservation_update() RESET search_path"
    )
