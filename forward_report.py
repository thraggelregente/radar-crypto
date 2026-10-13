"""Assess the immutable-strategy paper journal; never opens an order."""
import argparse
import json
from pathlib import Path
import sqlite3
import time
from backtest import bootstrap_lower
from config import Config


def report(path, cfg):
    db = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    try:
        s = json.loads(db.execute("SELECT value FROM state WHERE id=1").fetchone()[0])
    finally:
        db.close()
    if s["live"] or s["fingerprint"] != cfg.fingerprint():
        raise ValueError("Journal debe ser paper y coincidir con la estrategia")
    trades = s["trades"]
    if any(t["mode"] != "paper" for t in trades):
        raise ValueError("Journal mixto")
    returns = [t["return"] for t in trades]
    loss = -sum(min(t["pnl"], 0) for t in trades)
    gain = sum(max(t["pnl"], 0) for t in trades)
    pf = gain / loss if loss else (100.0 if gain else 0.0)
    lower = bootstrap_lower(returns)
    days = (time.time() - s["created"]) / 86400
    result = {"source": "paper journal", "fingerprint": cfg.fingerprint(), "generated": time.time(),
              "days": days, "observed_days": s.get("forward_observed_seconds", 0) / 86400,
              "trades": len(trades), "profit_factor": pf, "expectancy_lower_95": lower,
              "max_drawdown": s.get("forward_max_drawdown", 0), "approved": False}
    result["approved"] = (days >= 90 and result["observed_days"] >= 90 and len(trades) >= 50 and lower > 0 and pf >= 1.2
                          and result["max_drawdown"] <= cfg.max_drawdown and not s["halt"])
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("journal")
    p.add_argument("--output", default="forward_validation.json")
    args = p.parse_args()
    cfg = Config.from_env()
    result = report(args.journal, cfg)
    Path(args.output).write_text(json.dumps(result, indent=2, allow_nan=False))
    print(json.dumps(result, indent=2))
