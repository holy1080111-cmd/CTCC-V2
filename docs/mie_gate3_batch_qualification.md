# MIE Gate 3 — Public batch qualification

On 2026-09-02, the real External Benchmark Pack v3 probe completed from
reviewed source commit `1e23b252757f9c4f66e5574374ae19085171c633`. The
dataset remained outside Git. The probe downloaded only the 180 frozen public
Binance ZIPs and did not read an account, run a strategy, calculate strategy
costs, start a runtime consumer, or test an order.

## Verified evidence

| Item | Accepted value |
|---|---|
| Plan ID | `binance.btc_eth.1m.calendar_split.v1` |
| Canonical plan SHA-256 | `896f46a64848f74396314599ef68c756b60b43a0f77ec7ea797ce2fd97778634` |
| Serialized plan file SHA-256 | `4b59bb76e745494207dbca1b50808726bd3b9fa978f0b417d9f0db74405bd0eb` |
| Canonical preparation SHA-256 | `b2df82d17cd9dd987d06e29df5ec8f301d384f8631461dceb0cb7bd0420178e0` |
| Serialized preparation file SHA-256 | `032adf012b06eefb93811402cdbbf09ccfe105c8b72eee7a4ab3ab9bd62f360b` |
| Serialized final evidence SHA-256 | `aa06c4c5587c67dd363e691001304f8cb2f9d74bca35ddf08bf7dbc3e1c40d7a` |
| Completed artifacts | 180 of 180 |
| Verified ZIP bytes | 11,146,413 |
| Minute rows | 259,200 |
| Partition summaries | 6 |
| Partition overlaps | 0 |

The canonical contract hashes intentionally differ from the pretty-printed
file hashes. The former bind normalized model content; the latter bind the
exact evidence bytes stored outside the repository.

The committed qualification receipt is
`docs/evidence/mie_gate3_binance_batch_qualification_v1.json`. Its canonical
SHA-256 is
`baad6d40c42c6453a2c0e98f6b1818d62081a75547f24957d983f873dac60429`;
its exact file SHA-256 is
`1bfb33cdc2f30f8cca4a15a69cf07daa8a30a62d865523feaae4c630a2a32ed7`.

Revalidate the committed receipt alone with:

```powershell
python scripts/verify_mie_gate3_batch_qualification.py
```

An operator who has the outside-repository evidence root can additionally
rehash all 180 ZIPs and bind them back to the receipt:

```powershell
python scripts/verify_mie_gate3_batch_qualification.py `
  --dataset-root <outside-repository-dataset-root>
```

## Acceptance record

On 2026-09-02, implementation commit
`2f45cc34d1e9455ae3f35f6247fe5285194118bc` passed the isolated Windows
Docker Gate:

- all 180 outside-repository ZIPs were rehashed and matched the committed
  qualification, including the 11,146,413-byte total;
- 125 Gate 2/MIE targeted tests, 763 full regression tests, and 61 Gate 3
  foundation/qualification/boundary tests passed;
- Alembic head/current/schema drift passed at `0016`, API health was
  `healthy`, and the canonical manifest passed with 414 files;
- runtime containers had an internal-only network, no host API port, cleared
  proxies, empty exchange credentials, zero runtime consumers, and zero
  execution authority;
- the isolated containers, network, volumes, and image were removed after the
  run; the separately deployed stack was not reused or stopped.

The only emitted warning was the pre-existing Starlette `TestClient`
deprecation.

## Fail-closed holdout status

The batch probe emitted operator-visible descriptive summaries for every
partition, including the retrospective holdout. No candidate probability,
strategy return, baseline comparison, cost result, or trial selection was
computed, but the holdout is no longer human-unseen. Candidate design did not
predate that exposure.

The machine record therefore fixes:

```text
holdout_access_state=descriptive_summary_exposed
candidate_design_predated_holdout_access=false
predictive_oos_eligible=false
current_claim=computational
strategy_evaluated=false
costs_evaluated=false
reference_only=true
promotion_eligible=false
runtime_consumers=0
execution_authority=false
real_order_tested=false
```

The 2026-07-23 through 2026-08-21 partition may be used only for a labelled
pipeline rehearsal whose claim remains `computational`. A later
`predictive_oos` attempt must freeze its candidate, trials, parameters, costs,
and preregistration before exposing a fresh unread holdout. Gate 4 remains
blocked until that new evidence is independently reviewed.
