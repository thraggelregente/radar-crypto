"""Datos de mercado de Pionex (los mismos precios en los que se ejecuta la orden).

Regla de oro de este módulo: si un dato no se pudo bajar, se devuelve vacío/None.
NUNCA se inventan velas ni valores: una señal armada con datos falsos es peor que no tener señal.
"""
import requests
import pandas as pd

BASE_PIONEX = "https://api.pionex.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Pionex exige los intervalos en MAYÚSCULA: 1M, 5M, 15M, 30M, 60M, 4H, 8H, 12H, 1D
INTERVAL_MAP = {
    "1m": "1M", "5m": "5M", "15m": "15M", "30m": "30M",
    "1h": "60M", "4h": "4H", "8h": "8H", "12h": "12H", "1d": "1D",
}


def to_pionex_symbol(symbol: str) -> str:
    """'SOLUSDT' -> 'SOL_USDT' (si ya viene con guion bajo, lo deja igual)."""
    if "_" in symbol:
        return symbol
    for quote in ("USDT", "USDC", "BTC"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return f"{symbol[:-len(quote)]}_{quote}"
    return symbol


def _get(path, params=None, timeout=8):
    r = requests.get(f"{BASE_PIONEX}{path}", params=params, headers=HEADERS, timeout=timeout)
    try:
        return r.json()
    except ValueError:
        return {"result": False, "message": f"Respuesta no JSON (HTTP {r.status_code})"}


def get_klines(symbol="SOLUSDT", interval="15m", limit=100) -> pd.DataFrame:
    """Velas ordenadas de la más vieja a la más nueva. DataFrame vacío si algo falla."""
    sym = to_pionex_symbol(symbol)
    iv = INTERVAL_MAP.get(interval, str(interval).upper())
    try:
        res = _get("/api/v1/market/klines", {"symbol": sym, "interval": iv, "limit": limit})
        klines = (res.get("data") or {}).get("klines") or []
        if not klines:
            print(f"[datos] Sin velas para {sym} {iv}: {res.get('code') or ''} {res.get('message') or ''}", flush=True)
            return pd.DataFrame()
        df = pd.DataFrame(klines)
        needed = ["open", "high", "low", "close", "volume"]
        if not all(c in df.columns for c in needed):
            print(f"[datos] Formato inesperado de velas para {sym}: columnas {list(df.columns)}", flush=True)
            return pd.DataFrame()
        for c in needed:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        if "time" in df.columns:
            df["time"] = pd.to_numeric(df["time"], errors="coerce")
            df = df.sort_values("time")
        return df.dropna(subset=needed).reset_index(drop=True)
    except Exception as e:  # noqa: BLE001
        print(f"[datos] Error bajando velas de {sym}: {e}", flush=True)
        return pd.DataFrame()


def get_live_price(symbol: str) -> float:
    """Último precio de Pionex, o 0.0 si no se pudo obtener."""
    sym = to_pionex_symbol(symbol)
    try:
        res = _get("/api/v1/market/tickers", {"symbol": sym}, timeout=5)
        tickers = (res.get("data") or {}).get("tickers") or []
        if tickers:
            price = float(tickers[0].get("close") or 0)
            if price > 0:
                return price
    except Exception:  # noqa: BLE001
        pass
    try:
        res = _get("/api/v1/market/trades", {"symbol": sym, "limit": 1}, timeout=5)
        trades = (res.get("data") or {}).get("trades") or []
        if trades:
            return float(trades[0]["price"])
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def get_btc_change_1h(symbol="BTCUSDT"):
    """Variación % de BTC en la última hora (4 velas cerradas de 15m). None si no hay datos."""
    df = get_klines(symbol, "15m", 20)
    if len(df) < 7:
        return None
    closed = df.iloc[:-1]            # la última vela está en formación
    return float((closed["close"].iloc[-1] / closed["close"].iloc[-5] - 1) * 100)


def _to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _to_int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def get_symbol_rules(symbols):
    """Precisión y mínimos de cada par según Pionex. Devuelve {} si no se pudo consultar."""
    out = {}
    try:
        syms = ",".join(to_pionex_symbol(s) for s in symbols)
        res = _get("/api/v1/common/symbols", {"symbols": syms})
        for s in (res.get("data") or {}).get("symbols") or []:
            out[s.get("symbol")] = {
                "base_precision": _to_int(s.get("basePrecision")),
                "min_amount": _to_float(s.get("minAmount")),
                "min_size": _to_float(s.get("minTradeSize")),
            }
    except Exception as e:  # noqa: BLE001
        print(f"[datos] No pude consultar reglas de los pares: {e}", flush=True)
    return out
