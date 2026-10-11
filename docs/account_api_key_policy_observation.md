# Demo API-key policy observation (V1)

`account_api_key_policy.observe_v7_demo_api_key_policy` is an offline,
DENY-only interpretation of the original frozen V7 current-account packet.
It requires external SHA-256 pins for the frozen packet, plan and versioned
policy, then replays every original page through the existing V7 packet
verifier. It reads permission and IP fields only from the replayed
`account/config` rows before and after the capture. It does not accept a
caller-provided permission, IP list, account identity or approval boolean.

The policy checks that both complete config rows are identical, the exact
UID/main UID, account and position modes, and the copied credential-session
request context remain bound to the packet. `perm` must contain exactly
`read_only` and `trade`, with no `withdraw`, duplicates or unknown token. `ip`
must contain one or more unique global-unicast IPv4 literals under the fixed
V1 exclusion list. IPv6 and special-purpose IPv4 ranges remain unsupported;
they require a new reviewed policy version. Missing, malformed,
changed or unbound fields create fixed blocker codes. The receipt contains
only fixed booleans, blocker codes, policy/source digests and the two config
page receipt digests; it contains no raw UID, IP, permission string, source
row or credential. A saved receipt must be rechecked against the original
packet and its external hash pin before its observation is used.

This proves only consistency of the **supplied packet**. The copied
`session_binding_id` and full config-row equality are local replay checks;
they do not prove the same issuing API key or a provider session. The official
`perm` field can refer to the requesting API key **or Access token**. An
external hash pin and a simulated signed-request context do not prove that
the packet came from OKX, that the claimed registration site is correct, or
that the machine's current egress IP matches the configured list. The receipt
sets `observation_scope=supplied_packet_replay_only` and fixes
`registration_region_verified=false`, `source_authenticity_verified=false`,
`provider_credential_session_verified=false`,
`current_egress_ip_verified=false`, `account_complete=false`,
`execution_authority=false` and `admission=DENY`, even when both policy
fields are observed. It cannot promote the current packet, close history
gaps, create a `PortfolioRiskSnapshot`, or Arm Demo.

The [OKX account configuration reference](https://app.okx.com/docs-v5/en/#trading-account-rest-api-get-account-configuration)
documents `uid` as the current request account, `mainUid` as its parent,
`perm` as the current requesting API key **or Access token** permissions, and
`ip` as the API key's bound IP list (empty when unbound). The V7 packet and
existing receipt contracts remain unchanged; older recordings gain no new
trading authority.
