# Native public processing budget

The original public fixture used a virtual semantic clock. A separate diagnostic
kept its HTTP/WS responses unchanged and sampled the actual Windows native UTC,
monotonic clock and process CPU around capture and replay. On source f943f757,
capture took6.13seconds and replay1.47seconds; the observed ticker/WS arrival
age at the decision was7.40seconds. This exceeds the unchanged5-second policy.

Profiling identified the candle value guard as the dominant deterministic cost:
1.75million recursive calls used6.27of8.99profiled CPU seconds. The replacement
uses a depth-first iterative worklist and exact primitive-type fast paths.
Every occurrence, including repeated references to the same mutable model, is
visited on every call. Depth18, node120000, field/pin checks, sequence bounds,
Decimal rules, UTC checks and refusal order are preserved. No packet, model,
account, quote, eligibility, clock or receipt is cached.

A frozen hashed copy of the prior guard supports differential checks over
typed models, malformed primitives, cycles, shared-object mutations, traversal
budgets and500deterministic nested inputs. The original candle collector suite
also runs. The optimized prototype measured2.50seconds capture and0.62seconds
replay, with observed ticker/WS age3.05seconds under the same synthetic
transport. Exact-source measurements and subsequent full regressions are
retained separately.

These measurements show a CPU budget, not exchange/network latency acceptance.
Every genuine request, source timestamp, freshness check, publication barrier,
account read and final execution deadline remains independently measured.
Slow or changed source still fails closed; no timestamp or TTL is extended.
