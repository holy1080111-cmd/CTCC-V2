"""Read the independent bootstrap fitted data pair for the reviewed owner index source."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from scripts import evidence_json
from scripts import retained0056_fitted_reference_pair_v1 as reference

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES = _ROOT / "tests/fixtures/retained0056_bootstrap_fitted_source_v2"
_COMPONENTS = (
    (
        "scripts/evidence_json.py",
        "d26e80c2b54adba20fba488fe407ef8c2e8b984494953be5604e6ac053c041a4",
    ),
    (
        "scripts/retained0056_fitted_reference_pair_v1.py",
        "8535f414330dae1a77e42095b7550c65f57688bf078f2b1d16693754786b223a",
    ),
    (
        "scripts/retained0056_unscoped_fitted_observation_v1.py",
        "1a8b9bcb33ec5649b102e5a01a38982fbecb7df3e218c765d55f07627f4ef55f",
    ),
    (
        "scripts/native_owner_upgrade_0056_v1.py",
        "38ff34d9e06b3b73fb3a0b85b6f8c6c7d524bbc209a0dbc5af6e516c32cd7391",
    ),
    (
        "scripts/native_owner_granted_catalog_v1.py",
        "5ff2c622b8bb6e3f11e8b9c3a14917e82eaca476e9343f6f51fc267226e75af1",
    ),
    (
        "scripts/current0056_fitted_delta_catalog_v1.py",
        "2b91b135ba24345628649d491d3c829664f2c7320c59c197f30dac5c7af2dceb",
    ),
    (
        "migrations/versions/0056_gate3_fitted_candidate_custody.py",
        "82dacd7797d7e5f2286317fb2445c1a5e4986599733617d2ff8f3b8553b3ce3b",
    ),
)
_FILES = ("reference_a.json", "reference_b.json", "pair.json")
_PRIOR_SOURCE = {
    "commit": "ec95ea2157bda36f5ec5573f89bc5aad0541011b",
    "tree": "e34c0812e8d140271f734aa34f0245bcb0c951f0",
    "archive_sha256": "8bcdb2e24d929eab39eead0f1c3919868fadda74db49fd52d62deb247878f195",
    "manifest_sha256": "a54a72053d7d44d192314d2ae6d79dbfd1c53f73c5dd6901a2b6c3f9564d44b1",
    "runner_sha256": "242c954f239753d8b62425904be73ba194bcd04fca748986c659619201bf0784",
}
_OWNER_INDEX_SOURCE = {
    "commit": "4307ca0e897f6a88332cab1ea5125a77c64a75d8",
    "tree": "4808ecd9fe9356383b317ad15c5c1b1e9daf2eca",
    "archive_sha256": "5c5b7726882d3853c41d3fe8d2eb275dc40105a827cdf976754b9fd14cdfecee",
    "manifest_sha256": "ab3c3049459064a97ec1ec749571e858c33032c19a153cefebc9e8bad16349e2",
    "runner_sha256": "242c954f239753d8b62425904be73ba194bcd04fca748986c659619201bf0784",
}
_PRIOR_MEASUREMENTS = (
    "5e993ed3a3b0abfde2d949999c4fbdc03ecd82cf0092799edb63da3569577472",
    "abba652d38a2e23322ebe1cf96bcd4b60683152dd77bad730cc7b909f3b5f3c7",
    "57f2bd63e19a735e5c9fb323ec6fc3c5c4b6b9c46388157490c88c24002c0a43",
)
_OWNER_INDEX_MEASUREMENTS = (
    "35dc26c8f2e33515230f4de6fe23bf35269612f45956ed967209a8d607bcb2cf",
    "5ca2aa3ed1e9e31b27dd72a6cf1250f02e5bf06492955c2bc2e668a60ec7d9b6",
    "3782735c29f5da7b20a553177821fc9bbf30ef4ca7874a0fd49a3ad50cb4776d",
)


@dataclass(frozen=True, slots=True)
class ReviewedBootstrapFittedDataV2:
    structural_sha256: str
    logical_dependency_sha256: str
    fitted_context_role_sha256: str
    fitted_dependency_count: int
    reference_pair_sha256: str
    predecessor_reference_pair_sha256: str


def _pinned(path: Path, digest: str, *, max_bytes: int) -> bytes:
    reference.need(path.resolve(strict=True) == path and path.is_file())
    with path.open("rb") as stream:
        raw = stream.read(max_bytes + 1)
    reference.need(0 < len(raw) <= max_bytes and sha256(raw).hexdigest() == digest)
    return raw


def _read_pair(directory, source, hashes):
    first, second, archived = (
        _pinned(_FIXTURES / directory / filename, digest, max_bytes=8192)
        for filename, digest in zip(_FILES, hashes, strict=True)
    )
    pair = reference.compare(first, second, manifest=source["manifest_sha256"])
    reference.need(
        archived == reference.canonical({**pair, "validation_source": source})
    )
    return pair


def _clusters(pair):
    return {
        row["measurement"]["cluster_system_identifier_sha256"]
        for row in pair["references"]
    }


def read_reviewed_bootstrap_fitted_data_v2() -> ReviewedBootstrapFittedDataV2:
    for module in (evidence_json, reference):
        reference.need(
            Path(module.__file__).resolve(strict=True)
            == _ROOT / "scripts" / (module.__name__.rsplit(".", 1)[-1] + ".py")
        )
    for component, digest in _COMPONENTS:
        _pinned(_ROOT / component, digest, max_bytes=256_000)
    predecessor = _read_pair("predecessor", _PRIOR_SOURCE, _PRIOR_MEASUREMENTS)
    current = _read_pair("owner_index", _OWNER_INDEX_SOURCE, _OWNER_INDEX_MEASUREMENTS)
    reference.need(
        all(
            current[field] == predecessor[field]
            for field in (
                "structural_sha256",
                "logical_dependency_sha256",
                "role_catalog_sha256",
                "fitted_dependency_count",
            )
        )
        and current["fitted_dependency_count"] == 150
        and _clusters(current).isdisjoint(_clusters(predecessor))
    )
    return ReviewedBootstrapFittedDataV2(
        structural_sha256=current["structural_sha256"],
        logical_dependency_sha256=current["logical_dependency_sha256"],
        fitted_context_role_sha256=current["role_catalog_sha256"],
        fitted_dependency_count=current["fitted_dependency_count"],
        reference_pair_sha256=_OWNER_INDEX_MEASUREMENTS[2],
        predecessor_reference_pair_sha256=_PRIOR_MEASUREMENTS[2],
    )
