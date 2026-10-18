# Gate 3 four-hour public research dispatcher (INCOMPLETE)

`ScheduledPublicFourHourRunner` is a default-off, finite dispatcher for one
already sealed and database-acknowledged four-hour UTC window containing exact
BTC-USDT-SWAP and ETH-USDT-SWAP minute coordinates (480 ordinals). It has no
startup registration, background daemon, API route, account access or order
write. The caller must supply the existing service-owned receipt store, the
DB0030 seal observer, the DB0029 schedule reader, the owned public capture
service and the DB0035 observation reader. The runner never creates or changes
a seal, schedule, capture plan, receipt, risk state or trade.

At each due minute it samples native wall and monotonic clocks, waits in bounded
intervals, and invokes `drive_scheduled_public_minute_once` exactly once. That
existing owner repeats the committed seal/schedule preflight after the local
one-use claim, then performs at most one original public source capture and
DB0035 observation. A clock failure, miss, duplicate, unknown result, changed
source or changed committed seal/schedule preflight stops the window. A late
invocation retains the existing `late` original receipt and never tries to fill
the missing row.
Two instruments are dispatched in the schedule's fixed BTC/ETH order inside
the same minute's allowed interval; if the second is late, the window stops.

`ScheduledPublicPinChain` is a distinct native no-clobber root. It appends one
canonical, SHA-256-linked pin per original scheduled receipt, then reads it
back. The runner and each pin read/append require the receipt store's resolved
source path and native device/inode to match the pin identity; a different
source directory cannot be substituted under the same schedule. Each successful
pin binds native due UTC nanoseconds, original receipt digest, source capture
receipt digest, the last canonical raw-page receipt
digest, journal checkpoint, committed witness and DB0035 V3 digest. The raw
source journal is replayed to reconstruct its page chain before append and on
restart. The runner replays the retained source again after the due-time wait,
before a new one-use capture claim. Pin append also replays every prior original
source after the current claim and replays the full source and pin chain after
publishing. A detected change during capture or append stops continuation;
these separate local roots are not an atomic or independent custody boundary.
An incomplete original receipt can be pinned for audit, but cannot be
followed by another ordinal. A process crash after the original receipt but
before its pin leaves an unpinned source claim; restart refuses recapture and
does not infer the missing pin from the source files. Changed local pin or raw
source bytes also stop continuation. The pin replay does not requery historical
DB0035 observation or witness rows; it checks the digests retained by the
original capture. Full database re-read remains a promotion prerequisite.
Original V1/V2 scheduled receipts are unchanged.

The separate local pin root is **not independent custody**. The same host and
operator could alter its files and the original journal, and its wall clock
is not independently attested. Operational promotion requires a separately
administered append-only pin service with restricted credentials, protected
WAL/backups and readback; verified PostgreSQL/host UTC; an uninterrupted
acquisition owner; complete multiwindow two-symbol Development, Validation and
unexposed OOS coverage; an independently controlled first evaluator read; and
one formal sealed evaluation. The present runner does not provision or verify
those controls. Even a 480/480 replay returns `INCOMPLETE`,
`computational`, `independently_protected=false`,
`trusted_clock_verified=false`, `predictive_oos_eligible=false` and
`execution_authority=false`. It cannot produce a Gate 3 or OOS PASS.

The focused tests use synthetic service responses and a fixture-owned public
journal. They cover default-off behavior, two ordered due coordinates and
restart, original page-chain binding, late/duplicate/unknown outcomes, a crash
between source and pin, changed pins, root separation and a finite run stopping
at its first failed coordinate. They make no network request and do not run a
four-hour real-time window. A thermally unstable laptop is unsuitable as the
uninterrupted acquisition owner.
