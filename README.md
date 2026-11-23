# BTC Trend Rider — Race a BTC trading bot live for 24 hours

[![make.fun](https://make.fun/badge/btc-race-24h-20261123-0511.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511)
[![rank](https://make.fun/badge/btc-race-24h-20261123-0511/Victorcollin123/btc-race-24h-20261123-0511/rank.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511)
[![reward](https://make.fun/badge/btc-race-24h-20261123-0511/Victorcollin123/btc-race-24h-20261123-0511/reward.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511)

A bot that trades Coinbase Wrapped BTC (cbBTC) against USDC, minute by minute,
in a replay of its on-chain prices, for the
[make.fun bounty "Race a BTC trading bot live for 24 hours"](https://make.fun/bounty/btc-race-24h-20261123-0511).
Simulation only: it trades $1,000 of paper money against real on-chain prices;
no real money is traded.

## Strategy

Slow-trend riding, long only, at most one open position, full-size entries:

- **Indicators:** three EMAs over the 1-minute closes — fast 60, slow 120,
  long 240. Deliberately slow: the dev window's rally is a multi-hour grind,
  and fast EMAs on 1-minute bars churn in the flat chop that follows it (the
  kit's ema-cross baseline loses about $115 on the dev window on 76 trades).
- **Entry:** the fast EMA sits above the slow EMA with a gap of at least 0.1%
  of the close, the close sits above the long EMA, and the trailing four-hour
  return clears +1.5%. A four-hour window means flat chop that wiggles +0.4%
  in an hour cannot trigger an entry on its own.
- **Exit:** a 1.2% trailing stop off the best close since entry, a 10%
  take-profit safety valve, a 1.5% hard stop against cost, or the fast EMA
  crossing back under the slow one. After any exit the bot waits 120 bars
  before it may re-enter, so a stop-out in chop cannot turn into a new trade
  on the next bar.
- **Guard:** if equity ever falls 5% under its peak, the position is flattened.

## Dev window results (local replay, documented fill model)

One clean round trip — buy at bar 248 (~$59,794), sell at bar 433
(~$60,691) — for a **net PnL of about +$14.19 after all fees, 2 trades,
1.47% maximum drawdown**, deterministic across repeated runs. That clears
the dev gate (net PnL above $0). For reference on the same window:
buy-and-hold makes +$32.17 (1 trade) and the ema-cross baseline loses
−$114.83 (76 trades).

## How it runs

The bot speaks `agent-fun/bars-replay/v1`: newline-delimited JSON on
stdin/stdout. The harness sends `hello` once, then one `batch` per minute
(the bar that just closed, the fills of the previous orders, the account),
then `done`. The bot answers every batch with exactly one reply echoing its
`seq`, with its buy/sell orders. Orders decided on a bar fill at the next
bar's open.

```sh
python3 -P -s main.py
```

Python 3.13, standard library only. `vendor/makefun_bars.py` is a
byte-for-byte copy of the kit's Python SDK shim. Deterministic: no clock, no
network, no environment, no files outside the repository, no unseeded
randomness — all state is a pure function of the `hello` message and the bars
the harness has sent.

## Files

- `main.py` — the bot
- `vendor/makefun_bars.py` — the kit's SDK shim (byte-for-byte copy)
- `.makefun/owner.json` — the owner's linked wallet
