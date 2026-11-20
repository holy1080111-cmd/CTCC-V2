# Live market and finite financial composition

The explicit Live math adapter accepts either its original legacy Live DATA
inputs or the separate `LiveFinitePortfolioInputsV1` contract. These paths retain
different exact types and codecs. A finite account cannot be cast to a legacy
account or receive a fabricated historical loss seed.

For finite inputs, the financial risk policy must equal the original market
qualification policy. Both the unchanged candidate entry and current executable
reference run through the shared exact risk caps using the worst measured cost.
The higher loss sample remains binding. An unknown cashflow window still denies
G11, and a passing pure result still has no source, account, reservation or order
authority.

Validation: all twelve original Live raw-math tests passed in the first
composition run; its six new tests exposed errors in the synthetic date shift
and a test assertion against a field absent from the base result. After those
test-only corrections, all six new long/short composition tests passed. The
production calendar checks, quote tolerances and source timestamps were not
relaxed. These fixtures do not establish real account or Live acceptance.
