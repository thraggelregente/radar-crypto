"""Fail closed: validate candle clock, continuity, OHLC and executable quotes."""
import time
from pionex_client import finite


def validate_candles(rows, interval=3600, now=None, require_fresh=True):
    now = time.time() if now is None else now
    out = []
    for row in rows:
        t = int(row["time"]) // 1000
        if t % interval or t > now + 5:
            raise ValueError("Timestamp de vela inválido")
        bar = {k: finite(row[k], positive=k != "volume") for k in ("open", "high", "low", "close", "volume")}
        if bar["high"] < max(bar["open"], bar["close"], bar["low"]) or bar["low"] > min(bar["open"], bar["close"]):
            raise ValueError("OHLC inconsistente")
        bar["time"] = t
        # Determine closure from timestamps, never blindly drop the last row.
        if t + interval <= now:
            out.append(bar)
    out.sort(key=lambda x: x["time"])
    if any(b["time"] - a["time"] != interval for a, b in zip(out, out[1:])):
        raise ValueError("Velas duplicadas o con huecos")
    if require_fresh and (not out or out[-1]["time"] != int(now // interval) * interval - interval):
        raise ValueError("Velas desactualizadas")
    return out


class Market:
    def __init__(self, api, config):
        self.api, self.config = api, config
        self.rules = {}
        self.rules_ts = 0

    def load_rules(self):
        syms = tuple(dict.fromkeys((*self.config.watchlist, "BTC_USDT")))
        rows = self.api.public("/api/v1/common/symbols", {"symbols": ",".join(syms)}, weight=5).get("symbols")
        if not isinstance(rows, list):
            raise ValueError("Reglas no disponibles")
        rules = {}
        for r in rows:
            if r.get("symbol") not in syms:
                continue
            if not r.get("enable") or r.get("type", "SPOT") != "SPOT":
                continue
            for key in ("basePrecision", "amountPrecision"):
                if not isinstance(r.get(key), int) or not 0 <= r[key] <= 18:
                    raise ValueError("Precisión del exchange inválida")
            clean = dict(r)
            for key in ("minAmount", "minTradeDumping", "maxTradeDumping"):
                clean[key] = finite(r[key], True)
            rules[r["symbol"]] = clean
        self.rules, self.rules_ts = rules, time.time()
        return rules

    def candles(self, symbol):
        rows = self.api.public("/api/v1/market/klines", {"symbol": symbol, "interval": "60M", "limit": 500}).get("klines")
        if not isinstance(rows, list):
            raise ValueError("Velas no disponibles")
        return validate_candles(rows, self.config.interval_sec)

    def quote(self, symbol):
        rows = self.api.public("/api/v1/market/bookTickers", {"symbol": symbol}).get("tickers") or []
        row = next((r for r in rows if r.get("symbol") == symbol), None)
        if not row:
            raise ValueError("Libro no disponible")
        bid, ask = finite(row["bidPrice"], True), finite(row["askPrice"], True)
        stamp = finite(row["timestamp"], True) / 1000
        if ask < bid or not -5 <= time.time() - stamp <= self.config.max_quote_age:
            raise ValueError("Cotización cruzada o desactualizada")
        return {"bid": bid, "ask": ask, "time": stamp, "spread": (ask - bid) / ((ask + bid) / 2)}

    def entry_liquidity(self, symbol, amount, quote):
        rows = self.api.public("/api/v1/market/depth", {"symbol": symbol, "limit": 20}).get("asks") or []
        remaining, quantity = amount, 0.0
        previous = 0.0
        for row in rows:
            price, size = finite(row[0], True), finite(row[1], True)
            if price < previous:
                raise ValueError("Libro desordenado")
            previous = price
            take = min(remaining, price * size)
            quantity += take / price
            remaining -= take
            if remaining <= 1e-10:
                break
        if remaining > 1e-10 or quantity <= 0:
            return False
        vwap = amount / quantity
        return quote["ask"] * (1 - self.config.max_spread) <= vwap <= quote["ask"] * (1 + self.config.slippage)
