"""Typed role DATA for refusal/dispatch tests; never an observed database witness."""

from app.core.native_role_witness_0056_v1 import RestrictedNativeRoles0056WitnessV1, old
from app.domain import native_database_profile_v1 as profiles


def restricted_roles_fixture(profile=profiles.DB0056):
    profiles.checked(profile)
    observation = old.RestrictedNativeRoles0056ObservationV1(
        "1" * 64,
        "2" * 64,
        "3" * 64,
        "4" * 64,
        schema=profiles.schema(
            profile, "ctcc.native_only.restricted0056_role_observation.v1"
        ),
        database_head=profiles.head(profile),
    )
    return RestrictedNativeRoles0056WitnessV1(
        observation,
        "5" * 64,
        "6" * 64,
        schema=profiles.schema(
            profile, "ctcc.native_only.restricted0056_runtime_witness.v1"
        ),
    )
