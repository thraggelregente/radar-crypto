"""Public data only. Atomic manifest written only after all downloads succeed."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import time
from config import Config
from data_sources import Market, validate_candles
from pionex_client import Pionex


def download(directory, days=365):
    if not 30 <= days <= 400:
        raise ValueError("30–400 días; Pionex documenta un máximo de 10.000 velas")
    cfg = Config.from_env()
    api = Pionex(cfg)
    market = Market(api, cfg)
    rules = market.load_rules()
    now = int(time.time() // 3600) * 3600
    start = now - days * 86400
    files = {}
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    for symbol in tuple(dict.fromkeys((*cfg.watchlist, "BTC_USDT"))):
        cursor = now * 1000 - 1
        gathered = {}
        for _ in range(20):
            rows = api.public("/api/v1/market/klines", {"symbol": symbol, "interval": "60M", "limit": 500, "endTime": cursor}).get("klines")
            if not rows:
                break
            for row in rows:
                gathered[int(row["time"])] = row
            earliest = min(int(row["time"]) for row in rows)
            if earliest <= start * 1000:
                break
            if earliest >= cursor:
                raise ValueError("Paginación no avanzó")
            cursor = earliest - 1
        bars = validate_candles(list(gathered.values()), now=now, require_fresh=True)
        bars = [b for b in bars if b["time"] >= start]
        if len(bars) < 300:
            raise ValueError(f"{symbol}: historia insuficiente")
        out = io.StringIO(newline="")
        writer = csv.DictWriter(out, fieldnames=["time", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(bars)
        raw = out.getvalue().encode()
        (root / (symbol + ".csv")).write_bytes(raw)
        files[symbol] = {"sha256": hashlib.sha256(raw).hexdigest(), "rows": len(bars), "start": bars[0]["time"], "end": bars[-1]["time"]}
    manifest = {"source": "Pionex public API", "downloaded": time.time(), "interval_sec": cfg.interval_sec, "files": files, "rules": rules}
    temporary = root / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2, allow_nan=False))
    temporary.replace(root / "manifest.json")
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--directory", default="history")
    p.add_argument("--days", type=int, default=365)
    args = p.parse_args()
    download(args.directory, args.days)
