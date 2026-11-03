"""Fixed current54 hermetic subprocess/log contracts; old52 templates untouched."""

import re

from scripts import original_history_0052_toolchain as old52
from scripts import verify_final_hermetic as legacy

CURRENT54_READBACK_PROBE = """
import asyncio,json
from scripts.collector_current_0054_toolchain import _run
print(json.dumps(asyncio.run(_run('readback0054',None)),sort_keys=True))
"""
CURRENT54_RETENTION_PROBE = """
import asyncio,json
from scripts.collector_current_0054_toolchain import _run
print(json.dumps(asyncio.run(_run('retention0054',None)),sort_keys=True))
"""
PREDECESSOR_0047_IDENTITY_PROBE_CURRENT54 = old52._once(
    old52.PREDECESSOR_0047_IDENTITY_PROBE_0052,
    "before.head!='0052'",
    "before.head!='0054' or graph.get_revision('0054').down_revision!='0053' "
    "or graph.get_revision('0053').down_revision!='0052'",
)
PREDECESSOR_0047_IDENTITY_PROBE_CURRENT54 = old52._once(
    PREDECESSOR_0047_IDENTITY_PROBE_CURRENT54,
    "before=source_identity(SOURCE_ROOT)",
    "before=source_identity(SOURCE_ROOT)\n"
    "    from scripts.current_contract_source_scopes_v1 import current0054_source_scope\n"
    "    scoped=current0054_source_scope(SOURCE_ROOT)\n"
    "    if scoped.actual_migration_sha256!=before.sha256:\n"
    "        raise RuntimeError('current54_predecessor_source_scope_changed')",
)


def verify_current0054_downgrade_denial_log(path):
    legacy.verify_downgrade_denial_log(path)
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= 2 * 1024 * 1024
    ):
        raise ValueError("current54_downgrade_denial_log_missing")
    output = path.read_bytes().decode("utf-8")
    if re.findall(r"Running downgrade (\d{4} -> \d{4})", output) != [
        "0054 -> 0053",
        "0053 -> 0052",
        "0052 -> 0051",
        "0051 -> 0050",
        "0050 -> 0049",
        "0049 -> 0048",
        "0048 -> 0047",
        "0047 -> 0046",
        "0046 -> 0045",
        "0045 -> 0044",
        "0044 -> 0043",
        "0043 -> 0042",
        "0042 -> 0041",
    ]:
        raise ValueError("current54_downgrade_policy_refusal_unverified")
