# OKX Demo read-only authentication recheck (2026-10-16)

The existing local Demo credential was used only for one signed `GET
/api/v5/account/config` request per official region, with
`x-simulated-trading: 1`. A separate single request tested the legacy global
`www.okx.com` host. Redirects, proxies, retries and order writes were disabled.
No response body, UID, API key, secret, passphrase, signature or IP address is
retained in these receipts.

| Host | HTTP | OKX code | Complete account source |
| --- | ---: | --- | --- |
| `openapi.okx.com` | 401 | `50110` | No |
| `www.okx.com` | 401 | `50110` | No |
| `us.okx.com` | 401 | `50119` | No |
| `eea.okx.com` | 401 | `50119` | No |

The redacted three-region receipt is
`../../../validation-results/okx-region-serial-20261016/redacted-receipt.json`
(SHA256 `e8697b9ab0a4de1cf42fa31b9950b7b37753f8249d5f3df15942ebd73f09cb94`).
The `www` receipt is
`../../../validation-results/okx-www-readonly-20261016/redacted-receipt.json`
(SHA256 `202aa6279b097beabfcb56c85469203902b899bf5d13a8033aebda411e711411`).
An earlier 2026-10-12 three-region diagnostic had one successful global
`account/config` response and two regional `50119` responses; its redacted
receipt SHA256 is
`47096bf9a3a2a86f201168f2644e2ba5c29f6cbd52e3a81b631da53f205630c3`.
That historical response neither proves the registered site nor validates the
current credential or complete account history.

OKX currently documents different API hosts by registration region and says
`50119` usually indicates a region/domain mismatch. Its API FAQ also notes a
possible key-type incompatibility for `50110`; the code alone does not identify
the cause here. See the [OKX API guide](https://www.okx.com/docs-v5) and
[API FAQ](https://www.okx.com/en-gb/help/api-faq).

Current authenticated account identity, complete account collector, Demo
reconciliation and Demo acceptance remain **FAIL CLOSED**. This diagnostic
grants no execution, reservation, retry or source-authenticity authority.
