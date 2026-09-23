# Native Windows clock observation v2

This observation is a prerequisite for public acquisition, not trading authority. A caller-supplied observation or a successful pure replay cannot authenticate a native capture. Live and Demo flags are unaffected.

Historical `ctcc.windows_clock_observation.v1` English evidence retains its parser and canonical bytes. New native observations use the same outer fields with version `ctcc.windows_clock_observation.v2`; their `diagnostic` is the explicitly versioned `ctcc.windows_clock_diagnostic.v2` tree. Its host-before, status, and host-after probes each retain bounded raw stdout/stderr as canonical base64, byte count, SHA256, start/end clock samples, command identity, encoding, exit code, and complete/incomplete outcome. No repaired text replaces the original output.

## Supported resource profile

The only new profile is `ctcc.w32tm.26100_9278.en_US_zh_TW.cp950.v1`. It requires these exact files under the native Windows SystemDirectory, matching versions and valid Windows Authenticode status before and after the status query:

| Relative file | Raw numeric version | SHA256 |
| --- | --- | --- |
| `w32tm.exe` | 10.0.26100.9278 | `ce8721f50788d4041b278fe05d47c02c1f6718dd5390f316e4d8eccb20eaebc1` |
| `en-US/w32tm.exe.mui` | 10.0.26100.1 | `b9bf75e27b71fafa5aca897040282535fa789d8cecdf70a384f71d1b4c47e3ca` |
| `zh-TW/w32tm.exe.mui` | 10.0.26100.1 | `f246a33da5f287a6ffa905adbeae62c22782e8e1072db9c50519f603cd177d57` |

An unsupported build, absent resource, invalid signature, changed hash, or unknown encoding fails closed. A Windows update needs a fresh profile review; these hashes are not a generic Windows guarantee. The native helper uses fixed executable paths and explicitly loads only the official modules in that Windows PowerShell installation. It disables module autoload for that process, avoiding an inherited PowerShell 7 module path. Process-local progress output is suppressed; real stderr is always rejected.

Required STRINGTABLE IDs are 2501 (Leap Indicator / 躍進式指示器), 2502 (Stratum / 組織層), 2508 (Source / 來源), 2535 (Last Sync Error / 上次同步處理錯誤), and 2536 (Time since Last Good Sync Time / 自上次良好同步處理時間後的時間). The exact pinned EXE connects the unsigned `%I64u.%07I64us` formatter to resource 2536 through its `LoadStringW` resource-printing helper. Resource paths, source hashes, reviewed instruction offsets, and import bindings are recorded in the task's `w32tm-resource-schema-audit.md` and accompanying audit artifacts.

Native w32tm output is captured directly as bytes and strictly decoded as cp950 for this measured local profile. The earlier PowerShell pipeline decoded those OEM bytes with the forced UTF-8 console setting and damaged Traditional Chinese characters before JSON serialization. The new path does not use that conversion. It recognizes one complete English or Traditional Chinese field family, rejects duplicate/mixed required labels, and does not infer alternative languages. Age must have seven decimal places and literal ASCII `s`. Numeric status and source restrictions remain unchanged.

## Precise clock domain

The verified local Python 3.12 runtime reports 15.625 ms resolution for `GetSystemTimeAsFileTime()` and `GetTickCount64()`. Those coarse measurements cannot reliably satisfy the existing 1 ms sample bracket and 5 ms wall/monotonic drift gate. Windows samples now pair the official `GetSystemTimePreciseAsFileTime` API with QPC-backed `time.perf_counter_ns`. The ctypes declaration uses the documented two 32-bit DWORD FILETIME fields and a void function accepting a FILETIME pointer; it does not use W32Time RPC or adjust time. Conversion from the 1601 epoch uses integer 100 ns units.

The nested `ctcc.windows_precise_clock_domain.v1` metadata records both implementations, the FILETIME unit, the documented wall precision bound, and the actual pinned QPC resolution of 100 ns. Unknown, adjustable, nonmonotonic, or differently resolved counter implementations are rejected. Every Windows `native_stamp` in one public capture uses this same pair. Mixed v1/v2 OS observations in a capture are rejected. Historical v1 replay remains unchanged.

The precise wall API is documented by [Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-getsystemtimepreciseasfiletime), the [FILETIME structure](https://learn.microsoft.com/en-us/windows/win32/api/minwinbase/ns-minwinbase-filetime) specifies the epoch and units, and [Python 3.12 time documentation](https://docs.python.org/3.12/library/time.html) specifies the clock information and performance-counter interfaces. Precision does not prove the wall clock is synchronized; W32Time and independent exchange probes remain necessary.

## Admission and failure retention

Admission still requires Running=4, Automatic=2, exit code zero, Leap Indicator zero, Stratum 1–15, Last Sync Error zero, a permitted time source, a known timezone, valid UTC conversion, and last-good-sync age at most 900 seconds. The observation adds measured request-to-final-sample lag to that age conservatively. The full observation has a five-second lease. The 1 ms bracket and 5 ms drift limits are unchanged.

Each process stream has a 32 KiB retention bound. Overflow is explicitly truncated/incomplete, not a complete response. Timeouts, nonzero exit, stderr, reversed/jumped clocks, unknown profiles, or incomplete output deny admission. If an ending clock sample fails after bytes were read, the bytes remain retained and the completion timestamp is `None`; no timestamp is invented. There is no retry.

`NativeClockObservationError` has a fixed public error code and an explicit immutable `observation_bytes` attribute for diagnostic retention. Those bytes have no authority and are not included in normal exception text. This is in-memory retention, **not** a durable journal guarantee; a caller can persist the rejected observation through its approved evidence storage. A hard process loss before that persistence does not become a claimed durable observation.

Tests exercise synthetic English/Traditional Chinese replay, fully rehashed hostile inputs, native-process failures, bounded output, precise-clock source failures, and mixed-version public capture. They do not establish a healthy native clock or trusted TLS source. The actual implementation check on this host retained the 42-byte Traditional Chinese `0x80070426` response and confirmed Stopped=1 / Manual=3 before and after. It correctly denied admission. No service, locale, ACL, or clock setting was changed; no public exchange request was required for this check.
