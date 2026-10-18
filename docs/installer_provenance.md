# Installer and release dependency provenance

The historical HighVol installer at
`../checkpoint-20260919/installer-original/Install-CTCC-HighVol-Momentum-V2.ps1`
(SHA256 `a397bb5345e04e2d32c86e85fc862e776ccadfa6c30bbd36edb7ad9326c34cd9`)
was preserved byte-for-byte. Its invalid `$Mode:` interpolation was corrected to
`${Mode}:` in the reviewed copy. The historical file is not modified or executed.
The reviewed package is a repair artifact, not a release. The later
`../validation-results/highvol-installer-original-20261005.zip` preserves a
pre-October-5-edit package that already has `${Mode}:` and parses cleanly;
it is not the malformed September checkpoint.

`scripts/verify_highvol_installer.ps1` verifies the seven-file identity manifest and
uses the real PowerShell parser without executing the package. `-DryRun` performs
the same read-only verification. It does not install, recreate, restore, Arm, or
submit. Repeated success proves verification idempotence, not install/reinstall or
rollback acceptance.

The verifier pins the current reviewed package identity file to SHA256
`c24d5acdfe12b081579a459fd1e977647cf6ee90e64b1b60d02535551b578909`
before it reads the manifest. A replacement manifest with self-consistent file
hashes is therefore rejected by default; an explicitly supplied alternate
`-ExpectedIdentitySha256` is a separate, caller-owned trust decision and is used
only by isolated verifier fixture tests; release evidence must use the default
pin. A read-only PASS is never deployment
authority. The package remains blocked from deployment.
The verifier reports `default_pin_used`; fixture overrides show false, so a
caller cannot mistake a self-selected trust anchor for the canonical pin.
The identity JSON and installer PowerShell are each hashed and parsed from the
same captured byte array with strict UTF-8 decoding. A source path changing
between a hash read and a parser read cannot produce a false parser result.
The verifier also rejects reparse-point package roots, identity parents, identity
files, and package member files. It rejects unlisted files and subdirectories
under the package root, including hidden members; the pinned identity file may
reside inside or outside that root. This is a read-only package-set check, not
install/reinstall or rollback acceptance.

The preserved original installer targets an older image and restores the Demo
Arm/scheduler after a restart. The reviewed copy corrects that behavior by
remaining disarmed and blocking deployment by default, but still targets the
older image. Its strategy/protection changes require integration with the
canonical qualification, post-publication recheck, reservation, and intent chain.
The accepted final deployment must start disarmed and require fresh reconciliation.
Until that work passes, install/upgrade/rollback acceptance remains unproven.
The canonical settings model explicitly recognizes the older overlay variable
`CTCC_DEMO_HIGH_VOL_MOMENTUM_V2`, defaults it to false, and rejects true with
`CANONICAL_V2_DISPATCH_INTEGRATION_REQUIRED`. This prevents a carried-over
installer flag from being silently ignored at canonical startup. It neither
enables HighVol trading nor accepts the reviewed installer for deployment.

The separate, default-off [HighVol failed-G2 diagnostic](high_vol_g2_sidecar.md)
replays G1 source and an original event/timing pin without changing G2 or granting
trade authority. It is not an installer deployment route.

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

The September checkpoint script still reproduces the parser error at `$Mode:`;
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

## 2026-10-08 Windows dry-run timeout diagnosis

The earlier exact-source GitHub Windows run at `d5e71c4` timed out in the first
PowerShell 5.1 verifier subprocess after 30 seconds. Its log did not record a
verifier stage, so it does not establish whether startup, file checks, parser,
or module discovery stalled. A later four verifier cases passed quickly. The
verifier now offers optional fixed-name `-StageTrace` markers, and the test
records only the last safe stage if the same 30-second timeout recurs. It does
not increase the timeout, print package content or paths, weaken identity or
parser checks, or execute the installer. Local PowerShell 5.1 focused tests
passed 9/9; the timeout's actual cause remains unproven pending a matching
exact-source CI run.

## 2026-10-13 historical candidate re-audit

The September 15 `CANDIDATE_MATCH_FOUND` source can be narrowed to the extracted
`Demo-Integrated/907285d56e2045759c9219b300b52b6a/source` tree. The
preserved release contract independently matches all 582 source-manifest paths
and all 278 candidate members to that tree, with no missing or different file.
Its `deployment-result.json` selected `source_set=candidate`. The original
console line and the contract-named candidate and full-validation ZIP bytes were
not recovered in the bounded search, so their hashes and 9,011 historical test
passes remain contract claims rather than fresh archive verification.

The baseline and candidate `pyproject.toml` both hash to
`7a6962a2611ef95eb1f7cdb47ec3ef689aea0e0defb03e55b47cd21cdcae4bf3`.
The 39-versus-40 ordinary dependency inventory difference is exactly the
source-tree `ctcc-v2==1.6.9` egg-info record; isolated inventory is 39. The
narrow metadata exception in `verify_runtime.py` still rejects unrelated
package drift. Current canonical source has a different dependency file and
Linux/Windows lock identity, so this historical explanation never substitutes
for a full exact-current-source regression or release check. The independent
nonsecret audit record is
`../../validation-results/deployment-provenance-reaudit-20261013.md` (SHA256
`51d9d871948fb7db735c73a9ec2ceff5987f970fe4970185ded7606e99ff2692`).

## 2026-10-13 exact package-member check

The read-only verifier now rejects files and subdirectories outside the seven
identity-listed sources and the identity file itself. Its focused native
PowerShell tests pass 11/11, including extra-file and extra-directory rejection.
The retained `../reviewed-highvol-installer` working folder currently has an
unlisted `__pycache__` directory, so the stricter verifier rejects that folder
as a dirty package. A temporary copy containing exactly the eight pinned files
passed two identical read-only dry-runs and was removed afterward. No installer
or trading route was executed. The package remains blocked from deployment.

## 2026-10-18 latest reviewed rollback package

The isolated reviewed-v2 package in
`../validation-results/installer-reviewed-v2-rollback-20261018/package-final/`
fixes a partial `docker compose up` failure path by marking image replacement
uncertain before calling Docker, so the existing old-image rollback is attempted
if the command returns failure. Its seven-file identity is recorded in the
adjacent `package-identity.json` (SHA256
`c24d5acdfe12b081579a459fd1e977647cf6ee90e64b1b60d02535551b578909`).
The canonical verifier now pins this newer identity by default. Its output
explicitly says `deployment_authority=DENY`; no parser, hash or fake-Docker
result grants canonical trade qualification or deployment permission. The
package targets an older image and retains its default
`CANONICAL_V2_DISPATCH_INTEGRATION_REQUIRED` stop before Docker mutation.
The actual canonical HighVol entry policy still lacks a permitted historical
G2/G3 path and the common post-G12, reservation, intent and submit chain.
