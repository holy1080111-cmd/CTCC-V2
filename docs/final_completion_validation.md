# Final completion validation state

The 2026-09-19 final-completion branch begins at development commit
`016a3587476e83bb267558cf46998c501626ca3b`, tree
`0bcd60c53faf1ab8b0f290a7585fbeeb170540dc`. The separate deployment at
`C:\CTCC-V2` and its uncommitted changes are preserved. Neither a deployment
package date nor an old successful test count selects the source.

The initial source checkpoint contains both repositories' exact tracked working
files, unstaged/staged patches, Git bundles, refs/reflogs and hashes. Credential
files are excluded; only environment key names are recorded. Docker was offline,
so the database and authenticated account/exposure checkpoint remain pending.
No change to a deployed runtime is authorized by this source checkpoint alone.

## Corrected durable intent identity

The old PostgreSQL intent tests exposed a real symbol mismatch: the reservation
stores `BTC-USDT-SWAP`, while the domain candidate displays `BTC/USDT:USDT`.
Intent construction now compares the reservation with the exact instrument ID
already bound by the original prefix and contract specification. It does not
perform an alias substitution, accept another contract, or change the candidate.
The unit fixture now uses the same identity as PostgreSQL, and a display-symbol
receipt is explicitly rejected. Intent records still do not grant an order retry.

The original source reproduced six intent failures in a fresh, dedicated
PostgreSQL database. With the identity correction, all 29 reservation/intent
integration cases passed, including independent-session readback, concurrency,
rollback and uncertain-state retention. This is an intermediate working-source
result; final acceptance requires rerunning the committed source.

## Exact source Docker verification

Export the chosen commit with explicit LF conversion disabled:

```powershell
git -c core.autocrlf=false -c core.eol=lf archive --format=tar --output=source.tar <commit>
```

`scripts/verify_final_hermetic.py` reconstructs the actual Git tree from every
archived file and mode before extraction/building. An archive transformed to
CRLF is rejected even when a line-ending-normalized manifest would pass.
The archive must have the expected SHA256 and Git commit header. It rejects
credential paths, runtime reports, duplicates, traversal and links.

The Docker image contains the full manifest source set and validates it before
installing dependencies. Build products are excluded from the source manifest.
The runtime regression verifies the image's own COPY output; it no longer mounts
a host source directory and `.env` as a substitute. Every hermetic run creates
its own PostgreSQL, Redis and internal network, with no exchange credentials,
no host `.env` and no external network egress from the test containers.

The runner checks supported migration upgrade, downgrade/re-upgrade in the empty
test database, schema drift, PostgreSQL reservation/intent integration, and the
complete Linux suite. Its PASS is narrowly hermetic regression, not a statement
of statistical, account, Demo or Micro Live production acceptance. It also checks
API readiness and service restart with execution disabled; this is not a Live
restart/protection test. CI keeps the existing required job name and runs this
same exact-archive harness, retaining commit, tree, raw COPY map and step logs.
Those require independent evidence at the same final source identity.

## Conservative risk and preservation

Structural per-trade risk is capped at 0.5%, portfolio stop risk at 1%, with a
300-USDT default margin bucket. The 3/5/8/10/20x ladder remains a ceiling;
20x requires the existing mathematical, derivative, isolated-margin and cost
checks. The 60% aggregate margin gate counts held and reserved amounts and
rechecks exact reconciled exposure after leverage IO and at the final boundary.
Continuous scheduling cannot bypass daily loss or the persistent loss streak.

The final Demo HTTP entry transport currently refuses new entries because
trusted runtime qualification authority is not yet established. Manual, direct,
market, limit and FOK paths cannot bypass this containment with a caller PASS
or by mislabelling a POST as a read. Cancellation and close operations retain
their own existing safety checks. This containment is not completed Demo
acceptance or an enabled qualification pipeline.

Source formatting and Ruff diagnostics are being reconciled across the branch.
Local lint dispositions retain deliberate naive-time rejection fixtures,
immutable Decimal defaults, established ValueError validation contracts and
fail-closed IO exception handling. Previously silent auxiliary IO failures now
log exception class only, without account data, credentials or request bodies.

## Outstanding source-independent evidence

The host was measured behind OKX public time and NTP. Windows denied this process
permission to start W32Time. Timestamp tolerances and recorded receipt times have
not been altered. Public-source acceptance remains closed until the host clock
is repaired and directly remeasured.

Existing retrospective holdout summaries have already been exposed. They cannot
be resealed as unseen OOS or promoted from computational rehearsal to predictive
evidence. Engineering tests and genuine statistical acceptance remain separate.

Live remains default OFF. No current completion, release or trading acceptance
is asserted by this working document.
