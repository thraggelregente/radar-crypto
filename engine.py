"""A preregistered hypothesis, not an assertion of profitable alpha.

Hourly trend + confirmed breakout; same function used in live and historical tests.
"""
import statistics


def ema(values, period):
    alpha = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def atr(bars, period):
    tr = [bars[0]["high"] - bars[0]["low"]]
    for old, new in zip(bars, bars[1:]):
        tr.append(max(new["high"] - new["low"], abs(new["high"] - old["close"]), abs(new["low"] - old["close"])))
    # Wilder smoothing, with an arithmetic seed.
    value = statistics.mean(tr[:period])
    for item in tr[period:]:
        value = ((period - 1) * value + item) / period
    return value


def analyze(symbol, bars, btc_bars, cfg):
    required = max(cfg.slow + 100, cfg.breakout + 2)
    if len(bars) < required or len(btc_bars) < required:
        return {"eligible": False, "reason": "warmup insuficiente"}
    bars, btc_bars = bars[-required:], btc_bars[-required:]
    if bars[-1]["time"] != btc_bars[-1]["time"]:
        return {"eligible": False, "reason": "BTC y par no sincronizados"}
    closes = [b["close"] for b in bars]
    btc = [b["close"] for b in btc_bars]
    fast, slow = ema(closes, cfg.fast), ema(closes, cfg.slow)
    bfast, bslow = ema(btc, cfg.fast), ema(btc, cfg.slow)
    change = btc[-1] / btc[-2] - 1
    trend = closes[-1] > fast[-1] > slow[-1] and fast[-1] > fast[-4]
    btc_ok = btc[-1] > bfast[-1] > bslow[-1] and change > cfg.btc_brake
    level = max(b["high"] for b in bars[-cfg.breakout-1:-1])
    volume = statistics.mean(b["volume"] for b in bars[-cfg.breakout-1:-1])
    ratio = bars[-1]["volume"] / volume if volume > 0 else 0
    volatility = atr(bars, cfg.atr_period)
    stop = max(cfg.min_stop, cfg.atr_stop * volatility / closes[-1])
    target = cfg.atr_target * volatility / closes[-1]
    # Both legs incur fees and adverse execution. RR is a filter, not expectancy.
    cost = 2 * (cfg.fee + cfg.slippage) + cfg.max_spread
    rr = (target - cost) / (stop + cost)
    conditions = [trend, btc_ok, closes[-1] > level, ratio >= cfg.min_volume_ratio,
                  stop <= cfg.max_stop, target > cost, rr >= cfg.min_net_rr]
    return {"symbol": symbol, "eligible": all(conditions), "bar": bars[-1]["time"], "price": closes[-1],
            "atr": volatility, "stop_pct": stop, "target_pct": target, "net_rr": rr, "volume_ratio": ratio,
            "btc_change": change, "breakout_level": level, "rank": closes[-1] / level - 1,
            "reason": "ruptura confirmada" if all(conditions) else "filtros no cumplidos"}
