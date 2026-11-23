"""Slow-trend rider for the make.fun BTC race (agent-fun/bars-replay/v1).

One asset, CBBTC, long only, at most one open position, full-size entries.
The bot reads every bar the harness sends in each batch, keeps three slow
EMAs over the closes, and only buys when the trend is already proven over
hours, never on a blip: the fast EMA must sit above the slow EMA with a
real gap, the close must sit above the long EMA, and the trailing four-hour
return must clear 1.5%. That combination is what keeps it out of the flat chop where a
plain fast EMA cross churns itself to death (the kit's ema-cross baseline
loses about $115 on the dev window on 76 trades).

Once in, the exit is mechanical: a 1.2% trailing stop off the best close
since entry, a 10% take-profit safety valve, a 1.5% hard stop against cost,
or the fast EMA crossing back under the slow one. After any exit the bot waits out a
cooldown before it may re-enter, so a stop-out in chop cannot turn into a
new trade on the very next bar. A global equity drawdown guard flattens
everything if equity ever falls 5% under its peak.

Determinism: no clock, no network, no environment, no files, no unseeded
randomness. All state is a pure function of the hello message and the bars
the harness has sent, so two runs on the same data answer every batch with
byte-identical orders.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# EMA spans, in bars (1-minute bars). Deliberately slow: the dev window's
# rally is a multi-hour grind, and fast EMAs on 1-minute bars churn in the
# flat chop that follows it (the kit's ema-cross baseline loses ~$115 on
# 76 trades there). Slow spans wait the chop out.
FAST_SPAN = 60
SLOW_SPAN = 120
LONG_SPAN = 240

# Entry: the trailing 240-bar (four-hour) return must beat this, and the
# fast-slow gap must beat this share of the close, before a buy is even
# considered. A four-hour window means a flat chop that wiggles +0.4% in
# an hour cannot trigger an entry on its own, and the first entry cannot
# come before the trend has actually existed for hours.
MOMENTUM_BARS = 240
MOMENTUM_MIN = 0.015        # +1.5% over the last four hours
GAP_MIN = 0.001             # fast EMA at least 0.1% above slow EMA

# Exits.
TRAIL_PCT = 0.012           # 1.2% below the best close since entry
TAKE_PROFIT = 1.10          # position worth 10% over its cost (safety valve)
HARD_STOP = 0.985           # position worth 1.5% under its cost
EQUITY_GUARD = 0.95         # flatten if equity falls 5% under its peak

COOLDOWN_BARS = 120         # bars to wait after any exit before re-entering
MAX_SLIPPAGE_BPS = 100      # 1% past the close the order was decided on


class Ema:
    """One EMA: seeded as the mean of its first span of closes."""

    def __init__(self, span):
        self.span = span
        self.warm = []
        self.value = None

    def push(self, close):
        if self.value is not None:
            self.value += (2.0 / (self.span + 1)) * (close - self.value)
            return
        self.warm.append(close)
        if len(self.warm) == self.span:
            self.value = sum(self.warm) / self.span


class TrendRider:
    """Per-run state, derived only from the hello and the bars so far."""

    def __init__(self):
        self.fast = Ema(FAST_SPAN)
        self.slow = Ema(SLOW_SPAN)
        self.long = Ema(LONG_SPAN)
        self.closes = []            # closes, oldest first, raw price ints as floats
        self.peak_close = None      # best close since the current entry
        self.peak_equity = None     # best equity seen, in quote units
        self.cooldown_until = -1    # first seq on which re-entry is allowed
        self.in_position = False

    def on_bar(self, close):
        """Fold one bar's close into every indicator."""
        self.closes.append(close)
        self.fast.push(close)
        self.slow.push(close)
        self.long.push(close)

    def trend_up(self, close):
        """True only when the trend is proven on every check at once."""
        if self.fast.value is None or self.slow.value is None or self.long.value is None:
            return False
        if self.fast.value <= self.slow.value:
            return False
        if (self.fast.value - self.slow.value) < GAP_MIN * close:
            return False
        if close <= self.long.value:
            return False
        if len(self.closes) <= MOMENTUM_BARS:
            return False
        then = self.closes[-1 - MOMENTUM_BARS]
        if then <= 0:
            return False
        return (close - then) / then > MOMENTUM_MIN

    def should_exit(self, state, held):
        """True when any exit rule fires on this bar's close."""
        close = state.close(SYMBOL)
        if close is None:
            return False
        value = state.value(SYMBOL, held.qty)
        cost = held.cost
        if cost > 0:
            if value >= TAKE_PROFIT * cost:
                return True
            if value <= HARD_STOP * cost:
                return True
        if self.peak_close is not None and close < self.peak_close * (1.0 - TRAIL_PCT):
            return True
        if self.fast.value is not None and self.slow.value is not None:
            if self.fast.value < self.slow.value:
                return True
        if self.peak_equity is not None and state.equity < EQUITY_GUARD * self.peak_equity:
            return True
        return False


RIDER = TrendRider()


def on_batch(state, batch):
    # Every bar in this batch updates the indicators, in the order sent.
    for bar in batch["bars"]:
        if bar["symbol"] == SYMBOL:
            RIDER.on_bar(float(int(bar["c"])))

    # Track the equity peak for the drawdown guard.
    if RIDER.peak_equity is None or state.equity > RIDER.peak_equity:
        RIDER.peak_equity = state.equity

    # Warm-up bars build history only: orders decided on one are rejected.
    if state.warming_up:
        return []

    held = state.positions.get(SYMBOL)
    close = state.close(SYMBOL)
    if close is None:
        return []

    orders = []

    if held is not None:
        # We hold a position: update the trailing peak, then check exits.
        if RIDER.peak_close is None or close > RIDER.peak_close:
            RIDER.peak_close = close
        if RIDER.should_exit(state, held):
            orders.append({
                "side": "sell",
                "symbol": SYMBOL,
                "qty": held.qty,
                "max_slippage_bps": MAX_SLIPPAGE_BPS,
            })
            RIDER.cooldown_until = state.seq + COOLDOWN_BARS
            RIDER.peak_close = None
            RIDER.in_position = False
        return orders

    # Flat: only buy when the trend is proven and the cooldown has expired.
    if state.seq < RIDER.cooldown_until:
        return []
    if not RIDER.trend_up(close):
        return []

    room = state.weight_room(SYMBOL)
    stake = state.cash if state.cash < room else room
    min_notional = int(state.hello["fees"]["min_notional_quote_units"])
    if stake < min_notional:
        return []
    RIDER.peak_close = close
    RIDER.in_position = True
    return [{
        "side": "buy",
        "symbol": SYMBOL,
        "notional_quote_units": stake,
        "max_slippage_bps": MAX_SLIPPAGE_BPS,
    }]


def on_hello(state):
    # Nothing to seed from the hello besides the shim's own state; the
    # strategy is a pure function of the bars that follow.
    return None


makefun_bars.run(on_batch, on_hello=on_hello)
