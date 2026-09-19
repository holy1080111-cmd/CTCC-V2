# Reproducible validation dependency identity

The supported locks are CPython 3.12 on Windows amd64 and Linux x86_64/glibc:

- `requirements/validation-windows-py312.lock`
- `requirements/validation-linux-py312.lock`

Each pins all 40 selected wheel distributions and their SHA256 values. They
include the existing primary requirements, the existing `test` extra, the new
`validation` extra containing Ruff 0.16.8, pip 26.2.1 and setuptools 84.0.0.
Primary dependency declarations are unchanged. Platform resolution is native:
Windows includes colorama; Linux includes uvloop. These files are not universal
locks for other architectures, Python versions, or musl-based Linux images.

The matching JSON records the exact pyproject SHA256, resolver input, wheel
filename/size/hash, root wheel metadata hash, official PyPI release metadata hash
and official distribution URL. Actual wheel bytes were compared to official
non-yanked release records. Embedded metadata for a vendored package is not
mistaken for the enclosing wheel's identity.

Install into a fresh environment with the correct platform file:

```text
python -m pip --isolated install --require-hashes --only-binary=:all: -r requirements/validation-linux-py312.lock
python -m pip --isolated install --no-deps --no-build-isolation ".[test,validation]"
python -m pip check
python -B -m scripts.verify_dependency_lock --target linux
```

Use `windows` in both the filename and verification target on Windows. For an
offline installation, add `--no-index --find-links <verified-wheel-directory>`
to the first command and `--no-index` to the local source installation. The local
CTCC package is separately bound to the exact source archive/manifest, not fetched
from an index. Build isolation is disabled so setuptools is obtained from the
hashed lock rather than resolved through a second unpinned build environment.

pip's hash checking requires every dependency to be pinned and hashed; binary
only selection excludes unreviewed source builds. These constraints follow the
[official secure-install documentation](https://pip.pypa.io/en/stable/topics/secure-installs/).
The verifier checks the interpreter/platform, exact source and lock bytes, wheel
manifest links and complete installed package multiset. It reuses the existing
strict release verifier's sole permitted extra source-project metadata record.
No third-party deduplication, version substitution, or warning-only mismatch was
introduced.

## Regeneration

Use a new workspace and an isolated resolver environment. Prepare the inputs from
the current source declarations and pinned bootstrap tools:

```text
python -B -m scripts.generate_dependency_locks --workspace <new-workspace> --prepare
```

Run the following command natively on each supported platform, using that
platform's CPython 3.12. The Linux validation used the pinned official
`python:3.12-slim` image, not a Windows cross-platform marker approximation:

```text
python -m pip --isolated download --index-url https://pypi.org/simple --only-binary=:all: --no-cache-dir --dest <workspace>/<platform>-wheels -r <workspace>/validation.in
```

After download completion, independently verify the actual wheel bytes against
official PyPI JSON and generate the platform lock:

```text
python -B -m scripts.generate_dependency_locks --workspace <workspace> --target linux
python -B -m scripts.generate_dependency_locks --workspace <workspace> --target windows
```

Retain resolver logs, official metadata bytes, wheel hashes, fresh installation
results and negative hash tests as evidence. Re-run full validation for the new
exact source and locked environment. Regeneration is an explicit dependency
change review; it does not silently upgrade an accepted release environment.

## Acceptance boundary

The old September 15 Linux inventory contains 39 distributions and is not the
baseline for these locks. The independently measured Windows environment had
actual transitive differences, including greenlet and idna. Matching the primary
pyproject declarations did not make those inventories equivalent.

Every new lock provenance record and verifier result deliberately retains
`accepted_full_validation_baseline=false`. Hash-verified installation, successful
`pip check`, targeted tests, and an exact installed inventory establish dependency
reproducibility only. The final source commit must still pass full Windows/Linux,
PostgreSQL and hermetic Docker regression before a new release identity can be
accepted. Neither this verifier nor the lock generator grants trading authority.
