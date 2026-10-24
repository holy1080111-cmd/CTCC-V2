# Native V6 Development/Validation dataset

This additive builder creates actual deterministic minute aggregates and Gate 2
features. It does not certify Gate 3, OOS, an unseen holdout, a model, economic
edge, account state, qualification, or trading. All returned and published
datasets remain computational data with predictive and execution flags false.
No synthetic test is a real native source acceptance.

## Source and cutoff

`build_native_devval_dataset_v6` accepts the exact existing finite V6 collection
owner and its immutable source plan. Before the first acquisition, it pins its
own module, the additive V6 hook, the existing source/custody/clock functions,
the source manifest, and the complete packaged file inventories beneath
`app/mie`, `app/public_market_source`, `app/database`, `app/domain`, and
`app/trade_evidence`. Missing, additional, linked, replaced or changed source
files cause refusal. This is in-process packaged source verification, not
remote attestation or independent administration.

Each acquisition uses the original `capture_once` path, original finite ordinal
claim, Stage 1 originals, restricted custody and original one-use consumer.
The consumer burns before another await. Its decision cutoff is sampled once
by the existing consumer. The added hook retains the **same original private
state tuple** behind a separate nonportable binding; it does not reconstruct
an owner from saved fields, add a callback, renew a capability, or reset time.

The consumer's original 5-second lease is checked through materialization,
final source replay and the last synchronous return boundary. The original
120-second acquisition/claim budget remains a separate additional bound.
Both are preserved. A multi-capture build whose first original lease expires
fails closed. A large or slow acquisition may therefore have no successful
native result through this bounded API; making it feasible requires a separately
reviewed original acquisition design, not extending either deadline here.

All row availability remains the existing conservative native readiness upper
bound. Historical warmup acquired today is available today. This builder does
not set availability to bar close, manufacture older measured receipts, or turn
the previously exposed historical holdout into unseen data. The dataset's
`frozen_as_of` is the maximum of the original decisions, converted using the
existing conservative native UTC conversion; replay never reads the current
clock and never generates a replacement cutoff.

## Actual computation and complete coordinates

The plan permits Development and Validation only, with the fixed BTC and ETH
USDT perpetual pair. Every requested partition needs an exact, complete,
chronological two-symbol minute grid. Windows align to UTC 4H boundaries and
have at least 5,040 minutes per symbol, enough for the existing 21-bar 4H
feature dependencies. Original capture limits remain 3,000 rows per capture,
32 source-plan coordinates and the original journal/page/retention bounds.
The portable dataset has a separate 64 MiB ceiling that does not enlarge any
source or native custody limit.

Rows are not sorted, deduplicated, zero-filled or repaired. Missing, conflicting,
duplicate, reordered, out-of-window, swapped-instrument, mixed-environment,
wrong-unit or late coordinates cause refusal. `vol` remains `contracts` for
these SWAP rows. The implementation reuses
`aggregate_point_in_time_minutes`, `minute_source_sha256`, and
`replay_features_at`; it produces 15m, 1H and 4H OHLC/volume aggregates and actual
Gate 2 feature snapshots with the existing fixed arithmetic contexts.

The existing feature replay also rejects a missing or stale trailing bar at
the frozen decision. An older window is consequently not presented as a
current feature snapshot. In particular, two nonoverlapping historical windows
cannot be made causally fresh just by naming them Development and Validation.
This API is a bounded computational bridge, not a completed research cohort
or a substitute for genuinely supported prospective sampling.

## Final original source replay

The public original `CollectionReadOnlyV6.read` verifies saved files and Stage 1
lineage but does not reread the live databases. The additive hook therefore
performs a single reserved final replay attempt per original binding:

1. Recheck the original context, factory identities, claims and both deadlines.
2. Recheck the original restricted clock-custody deployment and actual DB0046
   authenticated scope.
3. Read the exact retained DB0046 bundle through its original custody owner,
   compare its bytes and proof pins, and replay the complete bundle.
4. Reread the actual complete witness chain, retaining the exact original
   prefix while allowing later legitimate immutable captures in that chain.
5. Reread the actual DB0035 observation through its original repository and
   compare every original observation field. A new readback timestamp remains
   a readback fact and does not replace observation time.
6. Replay the whole current journal and original retained collection slot;
   require every original packed file to remain byte-exact. All final group
   snapshots must agree.
7. After the last await, recheck source inventory and synchronously burn all
   bindings, replay the journal and check all original leases again.

There is no distributed transaction across the separate databases, no claim
that they remain unchanged after return, and no new runtime decision authority.
Unknown errors, cancellation or expiry retain existing evidence and burn the
attempt. They do not resubmit, remint the consumer, or retry acquisition.

## Immutable data and publication

The canonical dataset includes the exact source plan, dataset plan, manifest
and packaged source inventory digest; retained ordinal receipt, evidence and
raw bundle bytes and hashes; every original decision and hash; each raw row's
identity/hash and native measured availability; a length-framed ordered row
inventory hash; deterministic per-symbol aggregates and feature snapshots.
The full saved source/evidence/bundle lineage is replayed, rather than trusting
a caller's `passed` flag. The public pure materializer accepts supplied facts
only as computational data and cannot authenticate their acquisition.

`replay_native_devval_dataset_v6` reconstructs the same result from the saved
facts and compares the entire canonical output. It neither consults a new
clock nor issues a native capability.

`publish_native_devval_dataset_v6` uses the existing native no-clobber directory
publisher to write `dataset-v6.json`, `receipt-v6.json` and
`receipt-v6.sha256`, then reads each actual file back. The receipt binds dataset,
plan, source-plan, source-inventory and row-inventory identities. A partial late
failure preserves all successful durable files; retry does not overwrite them.
Publication and saved replay are inert data retention after native consumption,
with the existing platform/power-loss limitations. They do not renew a lease or
establish independently administered custody.

## Validation state

The prepared unit tests explicitly use synthetic I/O for the complete two-symbol
grid and run the real deterministic aggregate/feature functions. They cover
missing/conflicting/late coordinates, source pins, environment/units, changed
packaged inventory, immutable cutoff replay, one-use final replay reservation,
the original 5-second and original acquisition guards, and no-clobber retention.
The synthetic lineage decoder is not a production injection point. A separate
test requires those synthetic blobs to fail the real saved-lineage decoder.

At authoring time these are **prepared sources**, AST compiled only. They have
not been imported, run with pytest, acquired from OKX, or checked against real
PostgreSQL or native Windows clocks. Formatting, lint, meaningful test execution,
source integration, full regression and independent review remain required.
Real native/PIT/Gate 3/OOS acceptance remains pending. No existing source,
manifest, acceptance checkbox, credential or trading setting is changed by
preparing this patch.


## Prepared revision 3 corrections

R3 retains R1/R2 artifacts and fixes two independent source-review findings.
The runtime inventory now declares the actual transitive local module/class
closure in source, before acquisition. It pins helper aliases (including the
original denial helper and reducer leaves), function code and globals identity,
defaults/keyword defaults, and declared policy/SQL values. Original private
registries and closure state remain identity-bound; their mutable contents are
not copied into portable authority or frozen as policy. The original source
factory/clock/owner checks continue to validate those private states.
All local closure files also belong to the packaged source inventory.

Guards run before acquisition, immediately after each capture/final replay
await, through original lease checking and immediately before return. No
production path recaptures expected pins after observing a replacement.
Successful portable receipts now require reason `captured`, forensic status
`completed`, and the exact ordered forensic file inventory derived by replaying
the original retained Stage1 pack. Rehashing a contradictory receipt cannot
repair its completion semantics.

Additional tests use explicitly synthetic I/O baseline providers pinned before
acquisition, then replace real denial/math helpers or keyword defaults during
capture/final awaits. Separate portable-lineage tests obtain otherwise-valid V6
receipts through the existing synthetic native/DB fixture and rehash conflicting
reason/status/file inventories. Neither fixture is native or predictive proof.
R3 remains source preparation and stdlib AST inspection only, with no app,
pytest, database, network or native acquisition execution by the author.


## Prepared revision 4 constructor and model dispatch correction

R4 preserves the R3 archive and source review. The fixed class table now also
checks the effective MRO descriptors for inherited/generated constructors,
model validation/serialization and post-init routes, the relevant metaclass
dispatch, and the actual core validator/serializer objects and method slots.
Static descriptor lookup avoids pinning fresh bound method wrappers. Original
private WeakKeyDictionary registry contents may change normally; their exact
registry object identity remains pinned. No model rebuild, validation callback,
serialization or native operation runs while collecting these route pins.

Additional declared tests replace source-class, parent-model and generated
state constructors during capture/final replay awaits, and replace the model
validator dispatch object. They require refusal before the next consume or
materialization. Positive stability checks use normal model validation/dumps,
fresh classmethod wrappers and a temporary synthetic private registry entry.
The original 5 s consumer/120 s acquisition clocks, saved cutoffs, one-use burn,
full source replay and inert authority remain unchanged. This prepared revision
has only stdlib AST compilation and file/hash readback; no app imports, pytest,
database, native acquisition, network, Gate3/OOS or trading acceptance occurred.

## Canonical numeric input for deterministic dataset replay

The first actual isolated replay test exposed a representation mismatch:
Gate3 canonical JSON removes insignificant Decimal trailing zeros, while the
existing feature replay hashes its model JSON, which preserves that Decimal
representation. A first calculation using `100.50` and a saved replay using
`100.5` could therefore produce different provenance hashes despite identical
numeric values. The builder now validates each source group through its own
canonical JSON before invoking the original aggregation and feature functions.
Feature provenance consequently describes the canonical builder input.

This normalization preserves exact numeric values, original source/evidence
base64 bytes, source/receipt hashes and every saved decision cutoff. It changes
no original arithmetic context, source receipt, eligibility lifetime or
authority. The strict final byte comparison remains. The regression checks
same-context replay before replay under precision 7; actual validation of this
correction is pending, and the first failed source freeze and test evidence
are retained separately.


The bounded source reader retains each path and descriptor metadata identity,
including both independently stable ctimes. On Windows Python 3.12, path stat
reports the legacy creation-time ctime while descriptor fstat preserves change
time; cross-API identity therefore uses their common explicit birthtime. All
four samples must still be regular files with one link, no reparse attribute or
tag, and identical device, inode, size, mtime, mode, link and attribute metadata.
Missing Windows birthtime or attributes fail closed. POSIX retains strict
cross-API ctime equality. Neither timestamp is discarded from the inventory.
See the exact CPython 3.12.14 implementations in
[win32_xstat](https://github.com/python/cpython/blob/v3.12.14/Modules/posixmodule.c#L2015-L2017)
and [_Py_fstat_noraise](https://github.com/python/cpython/blob/v3.12.14/Python/fileutils.c#L1182-L1195).
The retained m11 fixture later showed this stable API difference; the original
failed-read tuples were not captured and its failed evidence remains preserved.
