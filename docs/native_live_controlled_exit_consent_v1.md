# Controlled micro exit consent

The optional exit selection requests a separate human confirmation:
`CLOSE_OKX_LIVE_MICRO_POSITION:<current nonce>`. The challenge exposes
`controlled_exit_seconds=120` when requested, or0 otherwise. The manual page
must show the120-second limit and maximum one close submission before approval.

The original worker accepts the exact phrase together with its current nonce.
The signal handler measures receipt with the native UTC and monotonic clocks.
The close deadline is fixed at that measurement plus120seconds. Entry Arm
lasts30seconds and has its own deadline; neither deadline renews the other.
Clock continuity uses the existing bounded measurement chain.

Only the original spent qualification, original physical entry handle, original
worker task and still-open operator roundtrip can consume the close consent.
Consumption occurs synchronously before the next await. The retained consent
binds exact UID/mainUID, credential session, reservation, original intent,
process and roundtrip. A saved consent dataclass remains DATA and cannot
construct a close owner. Stop, revocation, expiry or context closure denies it.

The source-owned close flow additionally requires a new public quote, new
account/position/protection readback, a separate durable close intent and
independent readback, a durable one-shot submission claim, and a final locked
physical boundary. The close is opposite-side, isolated, net-mode, reduce-only
FOK; its adverse price boundary is the original stop loss. Original protection
remains in place until a source-proven flat result. Any cleanup cancellation
needs its own exact-source gate after flatness is proven.

An unknown close result burns the submission opportunity, retains the hold and
requires reconciliation. Restart does not restore the consent or Arm. An
actual controlled close and Micro Live acceptance require the current human
authorization and observed exchange evidence. Consent-boundary unit tests do
not claim a venue order or that acceptance.
