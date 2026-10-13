"""Checksum-verified official SPOT archives. Cross-venue research ONLY.

No account, credentials or trading. Never labels Binance candles as Pionex.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import time
import urllib.request
import zipfile

from config import Config
from data_sources import validate_candles

BASE = "https://data.binance.vision/data/spot/monthly/klines"


def month_list(count, now=None):
    if not 1 <= count <= 60:
        raise ValueError("months debe estar entre 1 y 60")
    date = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)
    last = date.year * 12 + date.month - 2  # Last completed calendar month.
    return [f"{m//12:04d}-{m%12+1:02d}" for m in range(last-count+1, last+1)]


def timestamp_ms(raw):
    stamp = int(raw)
    if stamp >= 100_000_000_000_000:
        return stamp // 1000  # Spot archive switches to microseconds in 2025.
    if stamp < 100_000_000_000:
        raise ValueError("Unidad de timestamp inesperada")
    return stamp


def decode_archive(raw, checksum):
    expected = checksum.decode().strip().split()[0]
    actual = hashlib.sha256(raw).hexdigest()
    if len(expected) != 64 or actual != expected:
        raise ValueError("Checksum oficial no coincide")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].file_size > 20_000_000:
            raise ValueError("Archivo ZIP inesperado")
        lines = archive.read(members[0]).decode().splitlines()
    out = []
    for row in csv.reader(lines):
        if row and row[0].lower() in {"open_time", "opentime"}:
            continue
        if len(row) < 6:
            raise ValueError("CSV de velas inválido")
        out.append({"time": timestamp_ms(row[0]), "open": row[1], "high": row[2], "low": row[3],
                    "close": row[4], "volume": row[5]})
    return out, actual


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "radar-crypto-research/1.0"})
    # Retry reads only, at most twice. Do not hammer a refused endpoint.
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                raw = response.read(20_000_001)
            if len(raw) > 20_000_000:
                raise ValueError("Archivo demasiado grande")
            return raw
        except (OSError, TimeoutError):
            if attempt:
                raise
            time.sleep(.5)


def get_month(symbol, month, cache):
    pair = symbol.replace("_", "")
    name = f"{pair}-1h-{month}.zip"
    path = cache / name
    check = cache / (name + ".CHECKSUM")
    url = f"{BASE}/{pair}/1h/{name}"
    raw = path.read_bytes() if path.exists() else fetch(url)
    checksum = check.read_bytes() if check.exists() else fetch(url + ".CHECKSUM")
    rows, digest = decode_archive(raw, checksum)
    path.write_bytes(raw)
    check.write_bytes(checksum)
    return symbol, month, rows, {"url": url, "sha256": digest}


def download(directory="history_binance", months=36, min_amount=10.0):
    cfg = Config.from_env(research=True)
    if min_amount <= 0:
        raise ValueError("El mínimo hipotético debe ser positivo")
    root = Path(directory)
    cache = root / "archives"
    cache.mkdir(parents=True, exist_ok=True)
    symbols = tuple(dict.fromkeys((*cfg.watchlist, "BTC_USDT")))
    requested = month_list(months)
    grouped = {s: [] for s in symbols}
    provenance = {}
    jobs = [(s, month) for s in symbols for month in requested]
    with ThreadPoolExecutor(max_workers=6) as pool:
        pending = {pool.submit(get_month, s, month, cache): (s, month) for s, month in jobs}
        for number, job in enumerate(as_completed(pending), 1):
            symbol, month, rows, archive = job.result()
            grouped[symbol].extend(rows)
            provenance[f"{symbol}/{month}"] = archive
            if number % 12 == 0 or number == len(jobs):
                print(f"Archivos oficiales verificados: {number}/{len(jobs)}", flush=True)
    files = {}
    for symbol, rows in grouped.items():
        bars = validate_candles(rows, require_fresh=False)
        if len(bars) < 300:
            raise ValueError("Historia insuficiente")
        out = io.StringIO(newline="")
        writer = csv.DictWriter(out, fieldnames=["time", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(bars)
        raw = out.getvalue().encode()
        (root / (symbol + ".csv")).write_bytes(raw)
        files[symbol] = {"sha256": hashlib.sha256(raw).hexdigest(), "rows": len(bars),
                         "start": bars[0]["time"], "end": bars[-1]["time"]}
    # Explicit research assumptions: these are NOT retrieved Pionex trading rules.
    rules = {s: {"basePrecision": 8, "amountPrecision": 2, "minAmount": min_amount,
                 "minTradeDumping": 0.00000001, "maxTradeDumping": 1000000.0} for s in symbols}
    manifest = {"source": "Binance public archive", "downloaded": time.time(), "interval_sec": 3600,
                "files": files, "rules": rules, "archives": provenance,
                "rules_source": f"Unverified research assumptions: minAmount={min_amount}; NOT Pionex rules",
                "execution_venue": "Pionex not validated", "live_approval_allowed": False}
    tmp = root / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2, allow_nan=False))
    tmp.replace(root / "manifest.json")
    print(f"Listo: {root}. Datos reales de Binance; solo investigación, no aprobación de Pionex.", flush=True)
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--directory", default="history_binance")
    p.add_argument("--months", type=int, default=36)
    p.add_argument("--assumed-min-amount", type=float, default=10.0)
    args = p.parse_args()
    download(args.directory, args.months, args.assumed_min_amount)
