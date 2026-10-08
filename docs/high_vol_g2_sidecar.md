# HighVol failed-G2 diagnostic sidecar

`app/trade_qualification/high_vol_g2_sidecar.py` is an opt-in, offline
diagnostic. One call first runs the unchanged ordered G1–G7 prefix. Only when
G1 passes and G2 rejects a High Volatility breakout/expansion request with
`NO_TRADE` does it deserialize the market source produced by **that call's G1**
and invoke the versioned HighVol observation. The observation recomputes the
closed-bar event, timing, route and mathematical conditions from that source.

The caller must supply an exact original source/event/timing pin. This pin is
replayed and compared; its shape or SHA-256 is not proof that a real candidate
or trusted exchange capture existed. A missing pin rejects an enabled sidecar.
The observation's effective deadline can only shorten the original trigger and
candidate deadlines. A saved sidecar can be checked by replaying the original
market, quote, reference, policy, intent, pin and clock. Its receipt hash is a
consistency identifier. The sidecar exists in memory only: there is no native
immutable publication, independent custody or source authentication.

The default switch is off. The wrapper returns the original prefix unchanged;
the sidecar remains `DENY`, with `source_authenticity_verified=false`,
`qualification_performed=false` and `execution_authority=false`. It does not
create a passed G2, G5 event, entry zone, SL/TP, G12 report or order permit.
It has no Demo/Live runtime caller. Its current tests use synthetic OHLC and
MockTransport quote fixtures, so this is neither an authentic evidence example
nor HighVol installer, OOS, Demo or Live acceptance.

The reviewed September HighVol installer remains a separate, undeployable
repair artifact. Its snapshot-based scoring route and structural alignment
exception are not imported into the canonical qualification path. Any future
HighVol trading policy needs source-owned historical admission, independent
Gate 3/OOS and stress evidence, and the same canonical recheck, account, risk,
reservation, intent, protection and final submit boundary as every strategy.
