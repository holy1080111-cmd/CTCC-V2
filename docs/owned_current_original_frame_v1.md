# Private current original-input frame V1

`open_current_original_capture_v1` creates one local invocation from an active
runtime-issued Demo read lease, its exact private client, two different reviewed
V7 plans, four disjoint evidence roots, reviewed instrument/strategy, bounded
market policy, configured account database factory and restricted custody
factory. It constructs two distinct unused controlled sessions from that same
lease. It accepts no credentials, source packet, DTO, G1/candidate result, old
receipt, caller clock, callback, publication barrier or PASS override.

The returned capture object's `capture()` method synchronously removes its
registry entry before returning an awaitable. Abandoning or cancelling that
awaitable cannot make the invocation reusable. The original task, event loop,
process, thread, lease and private client must still match when it starts.

Inside that task the fixed coordinator calls the existing V4 native market
issuer, moves its one-use raw owner into the private frame, then calls and
consumes the existing V4 native account issuer. The new private keyed hooks
remove the original V4 registry entries, so the old public consumers and a
second hook cannot consume the same source. They do not accept saved bundles
or public result DTOs. Existing V4 public APIs, old policies, diagnostic bytes,
V7 flat-use revocation and their original acquisition paths remain unchanged.

The original source bytes and pins are retained privately across the account
acquisition's awaits. After the last database await, the frame independently
replays both original retained V4 bundles, rechecks current credential and
native owner context, and applies both original source freshness predicates to
a new native clock. It pins the configured account factory identity/native
engine/options, plus a new private restricted custody factory/repository,
before and after source phases and again at handoff. It does not accept a
caller repository with substituted instance methods. Shared hostile mutation
of native engine/class/global code remains outside this application boundary;
these checks are not a claim of arbitrary in-process compromise resistance.

The frame runs the unchanged fixed native G1 engineering inspection and its
independent raw verifier. It derives the existing raw V2 original precursor
using those exact public/account packets. The producer holds that result and
the full G1 bytes privately. `consume_current_original_inputs_v1` burns the
opaque frame before doing any work, then independently verifies the precursor
from the same raw packets at the **original** creation time and service
deadline. That verifier itself recomputes and verifies G1; its resulting G1
digest must equal the retained full G1 result. Full precursor bytes must also
match. There is no requirement to repeat an identical precursor computation
twice inside one phase. Original source freshness is checked at real handoff
time both before and after the mathematical replay.

An accepted handoff yields private measured original operands and an audit
record whose stage is `current_original_inputs_ready`. The returned operands
are a DTO, not a transferable capability; constructing or saving that DTO
cannot create another opaque capture/frame. Source packets, G1 and precursor
keep their existing contracts. In particular the raw precursor is still
incomplete and does not become qualified because its inputs were acquired in
one task. Its fixed G1 policy remains uncalibrated for trading.

No source expiry, score, risk limit, policy or timestamp tolerance is enlarged.
Sequential account acquisition can exhaust the earlier market's short current
quote/reference lease. Account freshness also retains its original UTC and
monotonic deadlines. Slow custody or pure computation can therefore reject
the frame after otherwise successful native collection; it does not refresh,
recollect or retry either source. Actual positive completion within those
leases must be measured on the eventual runtime. Synthetic-provider test
clock increments are not evidence that native acquisition/computation meets
the real elapsed budget.

Failure or cancellation releases the held market route claim and preserves
accepted native journals, raw custody and tombstones. The account source owner
is consumed once. Any error after frame consumption still leaves it burned.
`close()` discards only that local held frame; it does not delete evidence or
touch exchange/database state. Frames are process-local and cannot resume
after restart or be copied, pickled or reconstructed from database bytes.

This module is not wired to a route, scheduler or default execution path.
It calls no G12 publisher, reservation, intent or private write. Complete
portfolio history, funding/fees/HWM/streak and local exposure, account revision,
full G1–G11, post-G12 acquisition, event survival and protection remain separate
work. Account completeness, revision publication, candidate qualification,
post-G12 eligibility and execution authority remain false. Existing Demo/Live,
ledger owned-account-revision, DispatchOwnership and private-write DENYs are
unchanged.

The focused tests run both original V4 issuers and real raw replay/G1/precursor
functions with explicitly synthetic native transport/clock/storage/database
providers and genuine unconnected SQLAlchemy native engine profiles. They
exercise separate sessions, ownership moves, late readback failure,
sequential expiry, changed source/database pins, task/lease loss and burned
handoff reuse. They do not establish native TLS, OKX, PostgreSQL, current
account completeness, R7, Gate3/PIT, OOS, Demo/Live or release acceptance.
