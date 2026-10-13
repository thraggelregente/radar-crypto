import os
import json
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd
import vectorbt as vbt


DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# NOTE: No global EXCHANGE object here, we'll create per-attempt in fetch_ohlcv
# EXCHANGE = ccxt.binance({
#     "enableRateLimit": True,
#     "options": {"defaultType": "spot"},
# })


def fetch_ohlcv(symbol: str = "BTC/USDT", timeframe: str = "1h", limit: int = 2000):
    """Download OHLCV from several exchanges with fallback.

    Order of attempts:
      1) Binance public data-api (data-api.binance.vision)
      2) Binance normal
      3) KuCoin
      4) OKX
      5) Local CSV (if present)

    Returns a pandas.DataFrame or raises the last exception if all fail.
    """
    file_path = DATA_DIR / f"{symbol.replace('/', '_').lower()}_{timeframe}.csv"
    # If file already exists locally, use it (fast and deterministic)
    # You can force using local file by setting env var BACKTEST_FORCE_LOCAL=1
    if os.getenv("BACKTEST_FORCE_LOCAL", "0") == "1" and file_path.exists():
        print(f"[fetch_ohlcv] BACKTEST_FORCE_LOCAL=1 -> using local file {file_path}")
        return pd.read_csv(file_path, index_col=0, parse_dates=True)

    if file_path.exists():
        print(f"[fetch_ohlcv] Found cached file {file_path}, using it.")
        return pd.read_csv(file_path, index_col=0, parse_dates=True)

    attempts = [
        ("binance (data-api)", "binance", {"urls": {"api": {"public": "https://data-api.binance.vision/api/v3"}}}),
        ("binance (default)", "binance", {}),
        ("kucoin", "kucoin", {}),
        ("okx", "okx", {}),
    ]

    last_exc = None
    for name, ex_id, params in attempts:
        try:
            print(f"[fetch_ohlcv] Attempting {name} for symbol={symbol} timeframe={timeframe} limit={limit}")
            # Create exchange instance dynamically
            exchange_cls = getattr(ccxt, ex_id)
            ex_opts = {"enableRateLimit": True}
            # merge params (if empty it's fine)
            ex_opts.update(params or {})
            exchange = exchange_cls(ex_opts)

            # Some exchanges require load_markets() before fetch_ohlcv; ccxt will do it automatically.
            # fetch_ohlcv may raise ccxt.NetworkError, ccxt.ExchangeError, or HTTP errors.
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

            df = pd.DataFrame(ohlcv, columns=["Open time", "Open", "High", "Low", "Close", "Volume"])
            df["Open time"] = pd.to_datetime(df["Open time"], unit="ms")
            df = df.set_index("Open time")
            for col in ["Open", "High", "Low", "Close", "Volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna().sort_index()
            df.to_csv(file_path)
            print(f"[fetch_ohlcv] Success from {name} — saved to {file_path}")
            return df
        except Exception as e:
            # If we get a 451 or similar, print details and continue to next attempt
            print(f"[fetch_ohlcv] {name} failed: {type(e).__name__}: {e}")
            last_exc = e
            # continue to next provider

    # If we reach here, all remote attempts failed.
    # As a last resort, if local CSV exists, use it (even stale) or re-raise useful error.
    if file_path.exists():
        print(f"[fetch_ohlcv] Remote sources failed but found local file {file_path}, using it as fallback.")
        return pd.read_csv(file_path, index_col=0, parse_dates=True)

    # No data available
    print("[fetch_ohlcv] All remote exchanges failed and no local file present. Raising last exception.")
    raise last_exc or RuntimeError("Failed to fetch OHLCV from any source and no local file found.")


def rsi(series: pd.Series, period: int = 14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out[(avg_loss == 0) & (avg_gain > 0)] = 100.0
    out[(avg_loss == 0) & (avg_gain == 0)] = 50.0
    return out


def ema(series: pd.Series, span: int):
    return series.ewm(span=span, adjust=False).mean()


def backtest_strategy(df: pd.DataFrame, strategy_name: str):
    price = df["Close"]
    rsi14 = rsi(price, 14)
    ema_fast = ema(price, 9)
    ema_slow = ema(price, 21)

    if strategy_name == "ema_cross":
        entry = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
        exit_ = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))
    elif strategy_name == "rsi_ema":
        entry = (rsi14 < 35) & (ema_fast > ema_slow)
        exit_ = (rsi14 > 70) | (ema_fast < ema_slow)
    elif strategy_name == "pullback":
        entry = (rsi14 < 45) & (ema_fast > ema_slow) & (price > ema_fast)
        exit_ = (rsi14 > 65) | (ema_fast < ema_slow)
    else:
        raise ValueError(f"Unknown strategy: {strategy_name}")

    # Use vectorbt to backtest on close prices.
    entries = entry.to_numpy()
    exits = exit_.to_numpy()
    pf = vbt.Portfolio.from_signals(
        price,
        entries=entries,
        exits=exits,
        init_cash=10000,
        fees=0.001,
        slippage=0.0005,
        freq="1h",
    )

    stats = pf.stats()
    return {
        "strategy": strategy_name,
        "total_return": float(stats.get("Total Return [%]", 0.0)),
        "cagr": float(stats.get("CAGR [%]", 0.0)),
        "sharpe": float(stats.get("Sharpe Ratio", 0.0)),
        "max_drawdown": float(stats.get("Max Drawdown [%]", 0.0)),
        "win_rate": float(stats.get("Win Rate [%]", 0.0)),
        "trades": int(stats.get("Total Trades", 0)),
        "profit_factor": float(stats.get("Profit Factor", 0.0)),
    }


def main():
    df = fetch_ohlcv("BTC/USDT", "1h", 2000)
    for name in ["ema_cross", "rsi_ema", "pullback"]:
        result = backtest_strategy(df, name)
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
