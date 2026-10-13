"""Declared capital/minimum sensitivities, not parameter optimization or live proof."""
import argparse
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from backtest import historical_report, load_dataset
from config import Config


def evaluate(job):
    name, cfg, data, rules, manifest = job
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    report = historical_report(data, rules, cfg, manifest["source"], digest)
    report.update(scenario=name, capital=cfg.capital, order_cap=cfg.order_cap,
                  fee_per_side=cfg.fee, assumed_minimum=rules[cfg.watchlist[0]]["minAmount"],
                  rules_source="Unverified research assumptions, NOT Pionex rules", live_approval_allowed=False)
    return report


def run(directory, output="reports", parallel=True):
    cfg = Config.from_env(research=True)
    data, original, manifest = load_dataset(directory, cfg)
    if manifest["source"] != "Binance public archive":
        raise ValueError("Esta sensibilidad requiere fuente Binance explícita")
    cases = [("reported_14_22_min10", 14.22, 5.0, 10.0),
             ("counterfactual_14_22_min1", 14.22, 5.0, 1.0),
             ("hypothetical_100_min10", 100.0, 20.0, 10.0)]
    jobs = []
    for name, capital, cap, minimum in cases:
        rules = deepcopy(original)
        for rule in rules.values():
            rule["minAmount"] = minimum
        jobs.append((name, replace(cfg, capital=capital, order_cap=cap), data, rules, manifest))
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    if parallel:
        with ProcessPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(evaluate, jobs))
    else:
        results = [evaluate(job) for job in jobs]
    summary = []
    for r in results:
        (root / (r["scenario"] + ".json")).write_text(json.dumps(r, indent=2, allow_nan=False))
        row = {"scenario": r["scenario"], "capital": r["capital"], "assumed_minimum": r["assumed_minimum"],
               "source": r["source"], "approved": r["approved"], "history_days": r["history_days"], "oos_days": r["oos_days"],
               "oos": {k: v for k, v in r["oos"].items() if k not in {"trade_log", "closed_trades"}},
               "stress": {k: v for k, v in r["stress"].items() if k not in {"trade_log", "closed_trades"}},
               "benchmark_30pct": r["benchmark_buy_hold_30pct"], "benchmark_100pct": r["benchmark_buy_hold_100pct"],
               "positive_folds": r["positive_folds"]}
        summary.append(row)
    (root / "binance_proxy_summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False))
    print(json.dumps(summary, indent=2), flush=True)
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("directory", nargs="?", default="history_binance")
    p.add_argument("--output", default="reports")
    p.add_argument("--serial", action="store_true")
    args = p.parse_args()
    run(args.directory, args.output, not args.serial)
