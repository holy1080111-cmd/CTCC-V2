"""Select only a reviewed physical56 reference for an exact actual source.

The original source56 loader remains strict. Newer source scopes never turn a
physical57/58 database into physical56 or grant startup/execution authority.
"""

from pathlib import Path

from scripts import current0056_reviewed_owner_portable_v1 as previous
from scripts import current_contract_source_scopes_v1 as scopes

_SOURCE = Path(__file__).resolve().parents[1]


def load_reviewed_historical_owner56_reference_v1(source):
    if (
        type(source) is not type(Path())
        or source != _SOURCE
        or source.resolve(strict=True) != source
    ):
        raise ValueError("historical56_reference_source_refused") from None
    before = scopes.database0056_source_scope(source)
    if before.actual_source_head == "0056":
        reference = previous.load_reviewed_owner_portable_0056_v1(source)
    elif before.actual_source_head == "0057":
        from scripts.historical0056_owner_source_on_0057_v1 import (
            load_historical_owner56_reference_on_source57,
        )

        reference = load_historical_owner56_reference_on_source57(source)
    elif before.actual_source_head == "0058":
        from scripts.historical0056_owner_source_on_0058_v1 import (
            load_historical_owner56_reference_on_source58,
        )

        reference = load_historical_owner56_reference_on_source58(source)
    else:
        raise ValueError("historical56_reference_source_refused") from None
    if (
        type(reference) is not previous.ReviewedOwnerPortable0056V1
        or reference.grant_authority is not False
        or reference.startup_admission is not False
        or reference.execution_authority is not False
        or reference.admission != "DENY"
        or scopes.database0056_source_scope(source) != before
    ):
        raise ValueError("historical56_reference_source_refused") from None
    return reference
