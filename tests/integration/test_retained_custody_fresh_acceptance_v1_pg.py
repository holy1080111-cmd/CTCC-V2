"""Mandatory separate native-maintenance purpose, not ordinary SQL PG0-4.

Run only with an explicit private resource request and genuine root Linux
mount/network namespace venue. Missing resources are a failure, never a skip.
No retained production PG data is accepted or read by this fresh fixture.
"""

import os
import sys

import pytest

from scripts import retained_pg_bootstrap_custody_v1 as custody
from tests.retained_custody_fresh_resource_v1 import (
    assert_fresh_acceptance_data_v1,
    requested_fresh_resource_v1,
)


def _native_request():
    assert sys.platform == "linux" and os.geteuid() == 0
    return requested_fresh_resource_v1()


def test_actual_fresh_cold_bootstrap_migrates_once_and_stops_with_two_readbacks():
    request_file, _data = _native_request()
    result = custody.run_fresh_canonical17_acceptance_supervisor_v1(request_file)
    assert_fresh_acceptance_data_v1(result)


def test_fresh_resource_request_cannot_enter_preserved_backup_supervisor():
    request_file, _data = _native_request()
    with pytest.raises(custody.RetainedBootstrapRefused):
        custody.run_preserved_owner_supervisor_v1(request_file)
