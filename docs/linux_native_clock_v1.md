# Linux kernel-clock observation V1

`app.domain.linux_native_clock_v1` is an explicit, opt-in building block. It
does not change the Windows V1/V2 contracts, validators, hashes, native clock
dispatch, public collectors, account collectors, R5 policy or Gate 3 claims.
It never writes a clock, NTP parameter, service setting or namespace offset.

The acquisition API takes no clock, status, path, profile, callback, health
flag or credential. `observe_linux_clock_v1()` can issue a nonserializable,
single-use native token only for an exactly reviewed runtime profile.
`consume_owned_linux_clock_v1()` requires the original process/thread, repeats
native provenance and clock checks, and enforces the original five-second
lease. Pure `replay_linux_clock_observation_v1(bytes)` verifies evidence;
saved or caller-created bytes cannot become a native token.

## Exact native profile prerequisite

The V1 ABI is limited to little-endian Linux x86-64 LP64 glibc. The complete
208-byte `struct timex` layout, offsets, alignment, `timeval`, `timespec` and
clock IDs must match an actual C-header probe on the reviewed platform.
Other ABIs, musl and inferred layouts are unsupported. Two exact measured
profiles are approved for the narrow read-only kernel-observation scope:
`ctcc.linux.x86_64.glibc239.py3123.probe21bcabc.v1` and
`ctcc.linux.x86_64.glibc241.py31214.base2f17fc0.v1`. Their literal source
provenance pins the probe source/run/artifact, raw readback, runtime files,
symbols, exact container base where applicable, and primary LP64 declaration
comparison. The module never learns a hash from the running host and
immediately trusts it, and accepts no caller trust switch. Approval of these
measured profiles does not establish an actual later observation's health.

The original credential-free probes
[`probe_linux_clock_profile_v1.c`](https://github.com/holy1080111-cmd/CTCC-V2/blob/21bcabcdc412d0528d52a120b3cf50dd57050560/scripts/probe_linux_clock_profile_v1.c)
and
[`probe_linux_clock_profile_v1.py`](https://github.com/holy1080111-cmd/CTCC-V2/blob/21bcabcdc412d0528d52a120b3cf50dd57050560/scripts/probe_linux_clock_profile_v1.py)
are pinned to that immutable Git source. They record
actual C layout, glibc versions, mapped runtime file hashes, native symbol
origins/aliases, UTC zone identity and namespace offsets. Host and exact pinned
Docker-base measurements must remain separate. Docker shares its host kernel;
a correct-looking wall time does not excuse `TIME_ERROR`, `EPERM`, seccomp
denial or absent discipline. The probe must not request `CAP_SYS_TIME`, relax
seccomp or synchronize the clock to obtain a positive result.
The current worktree's Python copy has mechanical Ruff import/capture-output
formatting changes and therefore a different hash. It is not the original
measured Python probe and never replaces the original provenance pin.

Production profile admission requires root-owned, non-group/world-writable
runtime files with exact measured hashes and sizes; loaded device/inode and
executable mappings must agree. The exact file set must cover the executable,
libc, dynamic loader, `_ctypes` extension and libffi roles. A separately mapped
shared libpython is optional, but must be included and pinned when specified
by the reviewed profile. An arbitrary minimum file count is not a substitute
for these roles: the measured host has five such files and the container six.
Both the actual process executable and loaded `_ctypes` extension must agree
with the corresponding profile roles. Native `dladdr` must resolve each API to the
measured libc file, measured symbol and exact address. Loader overrides,
timezone environment overrides, Python trace/profile hooks, unsupported ABI,
changed boot identity, nonzero time-namespace offsets and source/profile drift
reject. These controls detect accidental substitution and the listed hooks;
they do not defend against a malicious kernel, root, or arbitrary hostile code
already executing inside the trusted Python process. Runtime files, container
image, host kernel and service owner remain explicit operating assumptions.

## Read-only state and conservative timing

The only adjustment API call is `adjtimex` with an entirely zeroed input,
including `modes=0`. The Linux manual documents zero as a query permitted to
ordinary users. V1 retains the exact 208 returned bytes, hash, return state and
immediate errno. Native API failures and nonadmitted kernel states retain
their status bytes in `LinuxKernelStatusError.observation_bytes` and issue no
token. Malformed evidence, uncertainty-budget and causal-time failures also
deny, but do not currently attach that raw-status property. An `EPERM` query
failure is not relabeled as an unsynchronized NTP state.

V1 requires `TIME_OK` and errno zero. `STA_PLL` controls PLL updates; its absence
is not the `STA_UNSYNC` flag and does not by itself mean an unsynchronized
kernel. Normal status values 0, 1, 0x2000 and 0x2001 are admitted for this
kernel-observation scope. `pll_updates_enabled` is derived exactly from the
returned `STA_PLL` bit and independently checked on replay. `STA_NANO` changes
only the documented unit interpretation. UNSYNC, CLOCKERR, leap insertion/deletion,
frequency hold, unsupported FLL/PPS flags, unknown status bits and every other
return state reject. It records upstream server identity and last upstream
sync time as **unknown**. `adjtimex` does not provide those facts. It does not
invent the Windows 900-second last-good-sync evidence or claim an independently
certified UTC clock.

Native `clock_gettime` reads REALTIME, MONOTONIC and BOOTTIME; each sample is
bracketed by monotonic reads at most one millisecond apart. Resolution must be
at most one microsecond. Integer UTC conversion avoids float timestamps.
Reversal, overlapping samples, a wall/monotonic jump or a changing
boottime/monotonic difference over the existing five-millisecond bound reject.
The existing five-second observation and five-millisecond deviation policies
are not increased. A full five-second lease is measured conservatively from
the initial bracket's start. Replay and token consumption use the same sample
sequence validator. The final consumption bracket cannot overlap its prior
bracket, and BOOTTIME cannot reverse even by an amount below the suspend
deviation limit.

The returned timex time must lie in its measured request/receipt bracket.
Microsecond representations remain an interval; no later nanosecond is
fabricated. A derived uncertainty budget includes kernel maxerror, magnitude
of correction, reported precision, clock resolution, full sampling brackets
and worst reported frequency-tolerance drift, rounded upward. It cannot exceed
the existing five-millisecond bound. Kernel error estimates and `TIME_OK` are
kernel observations, not independent proof that the upstream time is correct
or was recently synchronized.

## Scope and validation

Successful evidence always preserves `upstream_server=null`,
`last_upstream_sync_at=null`, `utc_certified=false` and
`execution_authority=false`. R5 or Gate 3 must separately verify the approved
runtime deployment, upstream freshness/source and applicable source policy.
This module does not collect market data, certify historical availability,
start a scheduler, qualify a candidate or authorize an order.

Focused unit tests use a private synthetic runtime/kernel seam and explicitly
synthetic profile. They cover the positive owned-token lifecycle, strict
replay, units, kernel states, errno, uncertainty, time jumps, suspend,
provenance drift, expired/reused tokens and unchanged Windows rejection.
They do not substitute for the real C/runtime probe, an actual healthy Linux
observation, native source integration or full Linux regression.

## Independent probe observations, not profile admission

Credential-free GitHub run
[39657125200](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/39657125200)
used exact source `21bcabcdc412d0528d52a120b3cf50dd57050560` and retained
separate host/container observations. The independently downloaded artifact
SHA256 is `72c0f6d19f18a29361c719dbda8877a4d6dc48e1cf8f0b4b6749ae4a4dea6a83`.
The host reported glibc 2.39, `TIME_OK=0`, status 0, errno 0 and kernel maxerror
1515 microseconds. The pinned Python base reported runtime glibc 2.41,
`TIME_OK=0`, status 0, errno 0 and kernel maxerror 3515 microseconds. These
values are retained observations, not a later synchronization health claim.

The C executable was compiled on the host against **glibc 2.39 headers** and
the same binary was run inside the glibc 2.41 container. Both runs reported
the compiled 208-byte timex layout and compatible calls. This does not prove
that C was compiled against the container's 2.41 headers. The official glibc
2.41 LP64 declaration is available for review, but that source review and this
binary compatibility observation must not be described as a native 2.41-header
compile. A separate retained primary-source comparison verified that the LP64
else-branch declarations from glibc tags 2.39 and 2.41 are byte-identical:
SHA256 `648ab2266b5657ccfd486ac25bd14cff87d3648965558bd9ce2ce1a737f385d0`.
The complete tag-2.39 header SHA256 is
`1a214847fbda711b4a14f211961593fcb2f5879b16f2f7115b2069dcca83acd3`;
tag-2.41 is
`feb7e3f78aadcf5aa89233175df74e74366b0a7e79bf70f29edd24b45e817a4b`.
That review plus the retained native probe supports the limited ABI profile
approval; it does not change the fact that container development headers were
not compiled. Unit seams do not supply native acceptance; the separate actual
module observation below retains its exact source and narrower scope.

## One native module observation command

`python -m scripts.observe_linux_clock_v1_once` accepts no arguments or caller
clock/profile input. It invokes acquisition once, consumes the owned result,
strictly replays the evidence, and requires a second consume to reject that
same token. The negative reuse check is not a second acquisition or a retry.

Stdout contains exactly one JSON record bounded to 128 KiB, including the
actual result, fixed failure code, original successful evidence as canonical
base64 bytes and its SHA256, optional original status-record base64 bytes and
hashes, profile identity and token-reuse result. Original bytes remain
unchanged and can be independently decoded, hashed and strictly replayed;
replay does not remint a token. Unavailable evidence stays null. Native
failures retain an available raw-status marker only when the exception
actually supplies a valid original record. An output-budget failure denies
without silently truncating a successful evidence payload.

Environment values, arbitrary exception text and tracebacks are not printed.
The command itself reads no configuration, credentials or network source;
the native clock module still enforces its fixed loader-override guards.
Exit 0 means this one actual kernel observation and token lifecycle passed;
exit 1 records an actual rejection or unexpected failure, and exit 2 rejects
arguments before acquisition. UTC certification, upstream freshness, source
authenticity, historical availability and execution authority remain false on
every output. A workflow must retain a rejection as such and cannot substitute
an expected result or a successful synthetic CLI test.

## Actual observation on source `1ff3548`

[Run 39660775735](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/39660775735)
executed exact source `1ff3548531283e85a7f79ab7852822a66ea19438`, tree
`399d193f9361874da543d3ace4452a8f743ccdc6`. Independent artifact readback
verified the server SHA256, ZIP inventory, all 1277 Git blobs, 1276 manifest
rows, image identity and original output hashes. The artifact SHA256 is
`0cc0c4a278322f560e8389b2f7e4fa1601e9f7ec6eca7b2746c433b95fbc1f63`.

The container acquired once, consumed its token and rejected reuse. Its
original payload passed exact-source strict pure replay with a 40831 ns
uncertainty bound. This accepts only that kernel observation. The host
rejected `linux_clock_profile_unsupported`, consumed no token and supplied no
raw status record. Its precise cause was not established, and no new profile
was learned or automatically admitted. The workflow remains a failure with
partial observations; neither the host nor UTC/upstream freshness is accepted.

The nonsecret independent receipt is outside source at
`../validation-results/linux-native-clock-1ff3548-20261022/receipt.json`,
SHA256 `8e0c2ae461b5a9f0403ab622795d210644ed9f9d10ce83992a1bcf7c0258dd32`.
No saved payload can recreate a source or execution token. A later integrated
source requires its own matching regression; this result does not validate
R5, Gate 3, Demo or Live.

Primary references:

- [Linux `adjtimex(2)`](https://man7.org/linux/man-pages/man2/adjtimex.2.html):
  query mode, units, status and return states; errno and read-only behavior.
- [Linux UAPI timex definitions](https://github.com/torvalds/linux/blob/master/include/uapi/linux/timex.h)
  and [glibc timex layout](https://github.com/bminor/glibc/blob/release/2.41/master/sysdeps/unix/sysv/linux/bits/timex.h):
  ABI fields, padding and numeric flags, subject to actual C-header verification.
- [Linux `clock_gettime(2)`](https://man7.org/linux/man-pages/man2/clock_gettime.2.html)
  and [kernel timekeeping](https://www.kernel.org/doc/html/latest/core-api/timekeeping.html):
  wall, monotonic and suspend-aware boottime semantics.
- [Linux time namespaces](https://man7.org/linux/man-pages/man7/time_namespaces.7.html):
  namespace offsets and the clocks they virtualize.
- [Linux `dladdr(3)`](https://man7.org/linux/man-pages/man3/dladdr.3.html):
  loaded symbol and shared-object origin observations.
