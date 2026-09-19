# Installer and release dependency provenance

The September 16 HighVol installer was recovered from the local installer cache,
preserved byte-for-byte with hashes, and its invalid `$Mode:` interpolation was
corrected to `${Mode}:` in a reviewed copy. The original external package is not
modified or executed. The reviewed package is a repair artifact, not a release.

`scripts/verify_highvol_installer.ps1` verifies the six-file identity manifest and
uses the real PowerShell parser without executing the package. `-DryRun` performs
the same read-only verification. It does not install, recreate, restore, Arm, or
submit. Repeated success proves verification idempotence, not install/reinstall or
rollback acceptance.

The legacy installer cannot presently be an accepted canonical deployment route:
it targets an older image and automatically restores the Demo Arm/scheduler on
success and rollback. Its strategy/protection changes require integration with the
canonical qualification, post-publication recheck, reservation, and intent chain.
The accepted final deployment must start disarmed and require fresh reconciliation.
Until that work passes, install/upgrade/rollback acceptance remains unproven.

The September 15 `DEPENDENCIES_DIFFER_FROM_FULL_VALIDATION` mismatch was caused by
normal Python stdin discovery including one extra `/app/ctcc_v2.egg-info` project
build metadata record. The independently isolated installed inventory was 39
records; normal discovery was 40. The recorded versions and Python matched the
full-validation contract exactly. No dependency upgrade was necessary.

`scripts/release_dependencies.py` carries the narrow repair into canonical source.
It compares full multisets, verifies an independent `python -I -B` inventory, and
permits exactly one matching source project metadata record only at the verified
root. Missing/extra packages, changed versions, additional duplicate records,
unknown paths, symlinks, and failed isolated discovery still reject the release.
It never grants execution authority. A final release still needs its own frozen
dependency contract and a complete regression at its exact source identity.
