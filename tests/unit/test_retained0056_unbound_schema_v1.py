from collections import Counter

import pytest

from scripts import current0056_fitted_catalog_pair_v1 as native_reference
from scripts import current0056_fitted_delta_catalog_v1 as fitted
from scripts import retained0056_unscoped_fitted_observation_v1 as canonical
from scripts.native_validation import retained0056_unbound_schema_v1 as subject


def test_unbound_dependency_query_is_the_exact_measured_artifact():
    raw = subject._dependency_query().encode("ascii")
    assert len(raw) == 6396
    assert subject._digest(raw) == subject._QUERY_SHA256


def _dependence_witness():
    previous = (1259, 16384, 1, 1247, 1043, 0, "n")
    fitted = (1259, 20000, 1, 1259, 16384, 0, "n")
    state = (Counter({previous: 2}), Counter({("shared",): 1}), Counter())
    target = subject._Fitted(
        "1" * 64, "2" * 64, "3" * 64, 1, frozenset({(1259, 20000)})
    )
    return state, target, previous, fitted


def test_unbound_dependency_preservation_matches_original_multiplicity():
    before, target, _, new_edge = _dependence_witness()
    graph, shared, initial = before
    subject._require_preserved_dependencies(
        before, (graph + Counter({new_edge: 1}), shared, initial), target
    )


@pytest.mark.parametrize("changed", ("collapse", "shared", "initial", "outside"))
def test_unbound_dependency_preservation_refuses_unreviewed_changes(changed):
    before, target, previous, new_edge = _dependence_witness()
    graph, shared, initial = (rows.copy() for rows in before)
    graph[new_edge] += 1
    if changed == "collapse":
        graph[previous] -= 1
    elif changed == "shared":
        shared[("altered",)] += 1
    elif changed == "initial":
        initial[("altered",)] += 1
    else:
        del graph[new_edge]
        graph[(1259, 19999, 0, 1259, 16384, 0, "a")] += 1
    with pytest.raises(subject.UnboundSchemaRefused):
        subject._require_preserved_dependencies(
            before, (graph, shared, initial), target
        )


@pytest.mark.parametrize("phase", tuple(subject._Phase))
def test_unbound_catalog_refuses_the_other_actual_transaction_phase(phase):
    opposite_is_reader = phase is subject._Phase.REHEARSE
    document = {
        "schema": fitted.SCHEMA,
        "head": "0056",
        "server_version": "170011",
        "server_build": native_reference.PG_BUILD,
        "actor": "ctcc",
        "login": "ctcc",
        "tls": True,
        "read_only": opposite_is_reader,
        "repeatable" if opposite_is_reader else "read_committed": True,
        "durable": True,
        "search_path": "pg_catalog, pg_temp",
        **{
            name: []
            for name in (
                *canonical._STRUCTURAL_FIELDS,
                *canonical._ROLE_FIELDS,
                "function_grants",
                "table_grants",
            )
        },
    }
    with pytest.raises(
        subject.UnboundSchemaRefused, match="^unbound56_native_fitted_context_changed$"
    ):
        subject._project_fitted(document, None, phase, None)
