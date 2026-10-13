"""Chronological portfolio simulation. No optimizer, same entry/risk functions.

OHLC does not reveal intrabar order: when both barriers are touched, STOP wins.
Entry uses next bar open, never the close that generated the signal.
"""
import argparse
import csv
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import random
import statistics
import time

from config import Config
from data_sources import validate_candles
from engine import analyze
from pionex_client import floor_string
from risk import equity, position_size, update_limits


def bootstrap_lower(returns, seed=20261013):
    if len(returns) < 20:
        return -1.0
    rng = random.Random(seed)
    estimates = []
    # Moving-block bootstrap preserves some serial dependence; not a PBO/DSR substitute.
    length = min(5, len(returns))
    for _ in range(1000):
        sample = []
        while len(sample) < len(returns):
            start = rng.randrange(len(returns) - length + 1)
            sample.extend(returns[start:start + length])
        estimates.append(statistics.mean(sample[:len(returns)]))
    return sorted(estimates)[24]


def summarize(trades, curve, initial):
    returns = [t["return"] for t in trades]
    profit = sum(max(0, t["pnl"]) for t in trades)
    loss = -sum(min(0, t["pnl"]) for t in trades)
    high, dd = initial, 0.0
    for value in curve:
        high = max(high, value)
        dd = max(dd, 1 - value / high)
    return {"trades": len(trades), "net_return": (curve[-1] / initial - 1) if curve else 0.0,
            "profit_factor": profit / loss if loss else (100.0 if profit else 0.0),
            "expectancy": statistics.mean(returns) if returns else 0.0,
            "expectancy_lower_95": bootstrap_lower(returns), "max_drawdown": dd,
            "win_rate": sum(r > 0 for r in returns) / len(returns) if returns else 0.0}


def simulate(dataset, rules, cfg, start=None, end=None):
    count = len(dataset["BTC_USDT"])
    start = max(cfg.slow + 100, start or 0)
    end = min(count, end or count)
    s = {"cash": cfg.capital, "positions": {}, "dust": {}, "orders": {}, "halt": "", "manual_pause": False,
         "high_water": cfg.capital, "day": "", "day_start": cfg.capital, "cooldown": {}}
    trades, curve, trade_log = [], [], []
    missed_minimum = 0
    symbols = [symbol for symbol in cfg.watchlist if symbol in dataset and symbol in rules]
    half_spread = cfg.max_spread / 2

    def sell(symbol, price, now, reason):
        p = s["positions"][symbol]
        size = min(float(floor_string(p["qty"], rules[symbol]["basePrecision"])), rules[symbol]["maxTradeDumping"])
        if size < rules[symbol]["minTradeDumping"]:
            return
        proceeds = size * price * (1 - half_spread) * (1 - cfg.slippage) * (1 - cfg.fee)
        removed_cost = p["cost"] * min(1, size / p["qty"])
        p["qty"] = max(0, p["qty"] - size)
        p["cost"] -= removed_cost
        p["realized"] += proceeds - removed_cost
        s["cash"] += proceeds
        if p["qty"] < rules[symbol]["minTradeDumping"]:
            if p["qty"] > 1e-12:
                d = s["dust"].setdefault(symbol, {"qty": 0.0, "cost": 0.0})
                d["qty"] += p["qty"]
                d["cost"] += p["cost"]
            t = {"symbol": symbol, "opened": p["opened"], "closed": now, "cost": p["total_cost"],
                 "pnl": p["realized"], "return": p["realized"] / p["total_cost"], "reason": reason}
            trades.append(t)
            del s["positions"][symbol]

    for i in range(start, end):
        now = dataset["BTC_USDT"][i]["time"]
        opening = {symbol: dataset[symbol][i]["open"] * (1 - half_spread) for symbol in symbols}
        opening_value = equity(s, opening, cfg)
        update_limits(s, opening_value, cfg, now)
        curve.append(opening_value)  # Opening losses cannot disappear after an intrabar recovery.
        # Handle observable opening gaps before allocating capital to another entry.
        for symbol, p in list(s["positions"].items()):
            raw = dataset[symbol][i]["open"]
            bid = opening[symbol]
            reason = "stop_gap" if bid <= p["stop"] else "target_gap" if bid >= p["target"] else "time" if now - p["opened"] >= cfg.max_hold_hours * 3600 else ""
            if reason:
                sell(symbol, raw, now, reason)
        signals = []
        for symbol in symbols:
            if symbol in s["positions"] or now - s["cooldown"].get(symbol, 0) < cfg.cooldown_sec:
                continue
            lookback = cfg.slow + 100
            d = analyze(symbol, dataset[symbol][max(0, i-lookback):i], dataset["BTC_USDT"][max(0, i-lookback):i], cfg)
            if d.get("eligible"):
                signals.append(d)
        signals.sort(key=lambda d: (-d["rank"], d["symbol"]))
        btc_open = dataset["BTC_USDT"][i]["open"]
        for d in signals:
            symbol = d["symbol"]
            ask = dataset[symbol][i]["open"] * (1 + half_spread)
            if ask < d["breakout_level"] or ask > d["price"] + 0.5 * d["atr"]:
                continue
            if btc_open * (1 - half_spread) / dataset["BTC_USDT"][i-1]["close"] - 1 <= cfg.btc_brake:
                continue
            amount = position_size(s, d, rules[symbol], cfg, opening)
            if not amount:
                missed_minimum += 1
                continue
            price = ask * (1 + cfg.slippage)
            qty = amount / price * (1 - cfg.fee)
            entry = amount / qty
            s["cash"] -= amount
            s["positions"][symbol] = {"qty": qty, "cost": amount, "total_cost": amount, "realized": 0.0,
                "entry": entry, "opened": now, "stop_pct": d["stop_pct"], "target_pct": d["target_pct"],
                "stop": entry * (1 - d["stop_pct"]), "target": entry * (1 + d["target_pct"])}
            s["cooldown"][symbol] = now
            trade_log.append({"symbol": symbol, "signal_bar": d["bar"], "entry_bar": now, "entry_price": price})
            # Runtime takes at most one new entry per tick, but may take the next later in this same hour.
            # Conservative historical model: one new entry per hour.
            break
        # Adverse synchronized intrabar marks are conservative OHLC estimates, not observed tick paths.
        adverse = {}
        for symbol in symbols:
            mark = dataset[symbol][i]["low"] * (1 - half_spread)
            if symbol in s["positions"]:
                mark = max(mark, s["positions"][symbol]["stop"] * (1 - cfg.slippage))
            adverse[symbol] = mark
        adverse_value = equity(s, adverse, cfg)
        curve.append(adverse_value)
        update_limits(s, adverse_value, cfg, now + cfg.interval_sec // 2)
        for symbol, p in list(s["positions"].items()):
            bar = dataset[symbol][i]
            stop_hit = bar["low"] * (1 - half_spread) <= p["stop"]
            target_hit = bar["high"] * (1 - half_spread) >= p["target"]
            if stop_hit:
                sell(symbol, p["stop"] / (1 - half_spread), now + cfg.interval_sec - 1, "stop")
            elif target_hit:
                sell(symbol, p["target"] / (1 - half_spread), now + cfg.interval_sec - 1, "target")
        closing = {symbol: dataset[symbol][i]["close"] * (1 - half_spread) for symbol in symbols}
        value = equity(s, closing, cfg)
        update_limits(s, value, cfg, now + cfg.interval_sec - 1)
        curve.append(value)
    if end > start:
        for symbol in list(s["positions"]):
            sell(symbol, dataset[symbol][end-1]["close"], dataset[symbol][end-1]["time"] + cfg.interval_sec - 1, "end_of_sample")
        closing = {symbol: dataset[symbol][end-1]["close"] * (1 - half_spread) for symbol in symbols}
        curve.append(equity(s, closing, cfg))
    result = summarize(trades, curve, cfg.capital)
    result.update(missed_entries_risk_or_minimum=missed_minimum, trade_log=trade_log, closed_trades=trades)
    return result


def load_dataset(directory, cfg):
    root = Path(directory)
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("source") not in {"Pionex public API", "Binance public archive"} or manifest.get("interval_sec") != cfg.interval_sec:
        raise ValueError("Fuente o intervalo no verificable")
    data = {}
    syms = tuple(dict.fromkeys((*cfg.watchlist, "BTC_USDT")))
    for symbol in syms:
        info = manifest["files"][symbol]
        path = root / (symbol + ".csv")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != info["sha256"]:
            raise ValueError("CSV modificado después de descargar")
        rows = list(csv.DictReader(raw.decode().splitlines()))
        for row in rows:
            row["time"] = int(row["time"]) * 1000
        data[symbol] = validate_candles(rows, cfg.interval_sec, require_fresh=False)
    first = max(bars[0]["time"] for bars in data.values())
    last = min(bars[-1]["time"] for bars in data.values())
    aligned = {s: [b for b in bars if first <= b["time"] <= last] for s, bars in data.items()}
    times = [b["time"] for b in aligned["BTC_USDT"]]
    if any([b["time"] for b in bars] != times for bars in aligned.values()):
        raise ValueError("Series históricas no sincronizadas")
    if len(times) < cfg.slow + 102:
        raise ValueError("Muestra insuficiente")
    if any(symbol not in manifest["rules"] for symbol in cfg.watchlist):
        raise ValueError("Faltan reglas: no se excluyen pares silenciosamente del backtest")
    return aligned, manifest["rules"], manifest


def historical_report(dataset, rules, cfg, source="synthetic", dataset_hash=""):
    n = len(dataset["BTC_USDT"])
    cut = max(cfg.slow + 100, int(n * 0.3))
    base = simulate(dataset, rules, cfg, cut)
    stressed = replace(cfg, fee=cfg.fee * 2, slippage=cfg.slippage * 2, max_spread=cfg.max_spread * 2)
    stress = simulate(dataset, rules, stressed, cut)
    folds = []
    width = max(1, (n - cut) // 3)
    for k in range(3):
        a, b = cut + k * width, n if k == 2 else cut + (k + 1) * width
        folds.append(simulate(dataset, rules, cfg, a, b))
    days = (dataset["BTC_USDT"][-1]["time"] - dataset["BTC_USDT"][0]["time"]) / 86400
    # Equal-weight static benchmark; 30% exposure is capital matched, not volatility matched.
    syms = [s for s in cfg.watchlist if s in dataset]
    benchmark = statistics.mean(dataset[s][-1]["close"] / dataset[s][cut]["open"] - 1 for s in syms) if n > cut else 0
    benchmark = (1 + benchmark) * (1 - cfg.fee) ** 2 * (1 - cfg.slippage) ** 2 / (1 + cfg.max_spread) - 1
    positive = sum(f["net_return"] > 0 and f["trades"] >= 20 for f in folds)
    data_end = dataset["BTC_USDT"][-1]["time"] + cfg.interval_sec
    approved = (source == "Pionex public API" and 0 <= time.time() - data_end <= 7 * 86400
                and days >= 180 and base["trades"] >= 100
                and base["expectancy_lower_95"] > 0 and base["profit_factor"] >= 1.2
                and base["max_drawdown"] <= 0.10 and stress["net_return"] > 0 and positive >= 2)
    return {"source": source, "fingerprint": cfg.fingerprint(), "generated": time.time(), "trials": 1,
            "data_end": data_end,
            "dataset_hash": dataset_hash, "history_days": days, "oos_days": (n-cut) / 24,
            "approved": approved, "oos": base, "stress": stress, "folds": folds, "positive_folds": positive,
            "benchmark_buy_hold_100pct": benchmark, "benchmark_buy_hold_30pct": benchmark * cfg.max_exposure,
            "limits": ["Fixed hypothesis; no parameter optimization or PBO/DSR computed", "OHLC cannot reconstruct order book or outages",
                       "Adverse intrabar portfolio marks assume synchronized lows; not a measured tick drawdown",
                       "Historical symbol rules are approximated by current rules", "One entry/hour; stop-first for ambiguous bars",
                       "No real-market profitability claim for synthetic data"]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("directory")
    p.add_argument("--output", default="validation.json")
    p.add_argument("--capital", type=float)
    p.add_argument("--order-cap", type=float)
    args = p.parse_args()
    cfg = Config.from_env(research=True)
    if args.capital is not None:
        cfg = replace(cfg, capital=args.capital)
    if args.order_cap is not None:
        cfg = replace(cfg, order_cap=args.order_cap)
    cfg.validate()
    data, rules, manifest = load_dataset(args.directory, cfg)
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    report = historical_report(data, rules, cfg, manifest["source"], digest)
    report["rules_source"] = manifest.get("rules_source", "Pionex public API")
    report["capital"] = cfg.capital
    report["order_cap"] = cfg.order_cap
    report["fee_per_side"] = cfg.fee
    Path(args.output).write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps({"approved": report["approved"], "oos": {k: v for k, v in report["oos"].items() if k not in {"trade_log", "closed_trades"}}}, indent=2))


if __name__ == "__main__":
    main()
