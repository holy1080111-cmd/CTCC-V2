"""Actual source58 observations of independently migrated physical57 G12 labs."""

from scripts import current0057_g12_measurement_v1 as measured
from scripts import current0057_hermetic_contract as historical
from scripts import current0058_hermetic_contract_v1 as current

_PRODUCER = "scripts/historical0057_on_current0058_observation_v1.py"


def historical_source_identity(identity, files, files_raw):
    source = current.source_identity(identity, files, files_raw)
    current._need(
        type(identity.get("run_id")) is str
        and len(identity["run_id"]) == 12
        and all(ch in "0123456789abcdef" for ch in identity["run_id"])
    )
    return source | {
        "database_head": historical.HEAD,
        "historical_prefix_sha256": historical.pins.MIGRATION_INVENTORY_SHA256,
        "historical_leaf_sha256": historical.pins.MIGRATION_SHA256,
        "measurement_producer_sha256": files[
            "scripts/current0057_g12_measurement_v1.py"
        ]["sha256"],
        "historical_observation_producer_sha256": files[_PRODUCER]["sha256"],
    }


def verify_historical0057_components(root, identity, files):
    from scripts import current0058_full_hermetic_contract_v1 as full

    current._need(
        full.requires_g12(
            identity.get("suite"), identity.get("postgres_component_index")
        )
    )
    source = historical_source_identity(identity, files, current.canonical(files))
    values = [
        measured.verify_measurement(
            full._read(root, path, 16_384), source=source, case=case[1]
        )
        for path, case in zip(historical.REPORTS, historical.CASES, strict=True)
    ]
    current._need(
        all(
            len({value[key] for value in values}) == 1
            for key in (
                "cluster_system_identifier_sha256",
                "inherited_native_definitions_sha256",
                "g12_definitions_sha256",
            )
        )
    )
    return {
        "schema": "ctcc.source0058_historical0057.observation.inert.v1",
        "source": source,
        "source_head": current.HEAD,
        "database_head": historical.HEAD,
        "reports_sha256": {
            path: current.digest(full._read(root, path)) for path in historical.REPORTS
        },
        "cluster_system_identifier_sha256": values[0][
            "cluster_system_identifier_sha256"
        ],
        "startup_admission": False,
        "execution_authority": False,
        "admission": "DENY",
    }
