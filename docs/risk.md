# Risk Engine v0.6

The risk layer is deterministic and has no order-submission capability.

It validates candidate expiry, score, risk/reward, daily and weekly realized loss,
peak-equity drawdown, consecutive losses, total/open-direction/correlation position
limits, minimum quantity, and maximum notional.

Position size is the lower of:

- account risk budget divided by entry-to-stop distance;
- configured maximum notional divided by entry price.

Approval only produces a `RiskDecision`. Paper and exchange brokers remain unavailable.

The optional Demo continuous-session adapter supplies zero daily realized loss
and zero consecutive losses to this generic engine, so those two entry stops
are not enforced in that mode. It still supplies the observed loss to the
weekly-loss check and the actual peak equity to the drawdown check. Position
stops, portfolio limits, and exchange execution safety are outside this pure
decision function and remain mandatory.
