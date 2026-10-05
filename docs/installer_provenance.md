# Installer and release dependency provenance

The September 16 HighVol installer was recovered from the local installer cache,
preserved byte-for-byte with hashes, and its invalid `$Mode:` interpolation was
corrected to `${Mode}:` in a reviewed copy. The original external package is not
modified or executed. The reviewed package is a repair artifact, not a release.

`scripts/verify_highvol_installer.ps1` verifies the seven-file identity manifest and
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

A separate Windows lock-environment check on 2026-10-04 found a different
condition: its temporary interpreter was missing the already-pinned
`tzdata==2026.4` distribution. The recorded repair installed only that locked
wheel (`c2169a8b0a7a5e9674da5a135ccdfb2b3e671b333ed9fed17b41f73c34476e81`);
the isolated package inventory then matched all 42 expected records and
`pip check` passed. Its evidence is bound to source HEAD
`037fca431657fd8c692b0ffa43e1539790d4ba60` in
`../../validation-results/canonical-source-reviewed-20261004-dependency-repaired-v10/`.
It repairs that task-scoped environment only: the record sets
`full_validation_current_source=false` and
`accepted_full_validation_baseline=false`. It does not rebaseline or accept the
current final-completion source; that exact source still needs its own complete
regression and release identity.

`scripts/release_dependencies.py` carries the narrow repair into canonical source.
It compares full multisets, verifies an independent `python -I -B` inventory, and
permits exactly one matching source project metadata record only at the verified
root. Missing/extra packages, changed versions, additional duplicate records,
unknown paths, symlinks, and failed isolated discovery still reject the release.
It never grants execution authority. A final release still needs its own frozen
dependency contract and a complete regression at its exact source identity.

## 2026-10-05 current-source installer verification

At canonical HEAD `caa1588bb6dd8174c0f4eea4ceb9236f0004a7c9`, all 33 PowerShell scripts in `scripts/` parsed without errors, and the reviewed installer package passed its native PowerShell parser and exact seven-file identity check in read-only dry-run mode. The unscoped `$name:` interpolation scan found no matches; environment and script-scope variables were excluded as valid PowerShell syntax. No installer, deployment, Docker command, or exchange request was invoked.

The focused installer validator passed all 5 failure-path and dry-run cases with zero failures, errors, or skips. JUnit `../validation-results/highvol-installer-exact-source-20261005.xml` has SHA256 `c249501abb6fe61be422d8e81109af26b4f7fe6a055b2f41371fb949ee1eef03`. PSScriptAnalyzer is unavailable on this host. Install, reinstall, upgrade, and rollback remain untested because the legacy package is not integrated with the canonical qualification/recheck/reservation/intent chain and may restore Arm/scheduler state; this result is not installer deployment acceptance.
A second complete scan over the seven-file reviewed package found one colon-form
PowerShell variable reference, `$env:LOCALAPPDATA`, which is a valid environment
drive/scope expression. The malformed `$Mode:` interpolation is absent from the
reviewed copy; its failure was preserved in the original recovered source. The
package-wide PowerShell parser pass and the canonical verifier dry-run both
passed on 2026-10-05. PSScriptAnalyzer is not installed. No installer code was
executed; this does not accept install/reinstall/upgrade/rollback or canonical
trading integration.

## 2026-10-06 reviewed-package verification

The original preserved script still reproduces the parser error at `$Mode:`;
the reviewed script uses `${Mode}:` and parses with zero errors (2,529 tokens).
All seven package members match their pinned source hashes. Two repeated
read-only identity/dry-run checks and 11 focused validator/controller tests
passed; the final reviewed package identity SHA256 is
`34ef5b12fa3855977d784d31803e0e1c7cb871959609f68a29cca2efcbffd722`.
The reviewed package README now explicitly describes verification only and its
default deployment guard. PSScriptAnalyzer is unavailable on this machine.

No install, reinstall, upgrade, rollback or canonical qualification integration
was executed. These remain `NOT_ACCEPTED`; the reviewed package is retained as
repair evidence and cannot be treated as a V2 deployment path.
