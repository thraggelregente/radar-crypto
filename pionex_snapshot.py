"""Public rules/quotes and budget feasibility. No account, keys or orders."""
import argparse
import json
from pathlib import Path
import time
from config import Config
from data_sources import Market
from net import APIError
from pionex_client import Pionex
from risk import position_size


def snapshot():
    cfg = Config.from_env(research=True)
    market = Market(Pionex(cfg), cfg)
    rules = market.load_rules()
    quotes = {s: market.quote(s) for s in (*cfg.watchlist, "BTC_USDT")}
    state = {"cash": cfg.capital, "positions": {}, "dust": {}, "orders": {}, "halt": "", "manual_pause": False}
    sizes = {}
    for symbol in cfg.watchlist:
        if symbol not in rules:
            sizes[symbol] = {"status": "pair unavailable"}
            continue
        sizes[symbol] = {str(stop): position_size(state, {"symbol": symbol, "stop_pct": stop}, rules[symbol], cfg, {})
                         for stop in (.01, .02, .04)}
    return {"source": "Pionex public API", "generated": time.time(), "reported_capital": cfg.capital,
            "fee_assumption_per_side": cfg.fee, "account_fee_verified": False,
            "rules": rules, "quotes": quotes, "hypothetical_sizes_by_stop": sizes,
            "note": "Sizes illustrate budget compatibility, NOT buy signals. No private requests or orders."}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="pionex_public_snapshot.json")
    args = p.parse_args()
    try:
        result = snapshot()
        Path(args.output).write_text(json.dumps(result, indent=2, allow_nan=False))
        print(f"LISTO: {args.output}. Solo informacion publica, sin claves ni operaciones.")
    except (APIError, ValueError, KeyError) as e:
        print(f"No se pudo obtener informacion publica: {type(e).__name__}: {e}")
        raise SystemExit(1)
