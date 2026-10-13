import os
import json
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd
import vectorbt as vbt


DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)

EXCHANGE = ccxt.binance({
    "enableRateLimit": True,
    "options": {"defaultType": "spot"},
})


def fetch_ohlcv(symbol: str = "BTC/USDT", timeframe: str = "1h", limit: int = 2000):
    """Download OHLCV from Binance spot using CCXT."""
    file_path = DATA_DIR / f"{symbol.replace('/', '_').lower()}_{timeframe}.csv"
    if file_path.exists():
        return pd.read_csv(file_path, index_col=0, parse_dates=True)

    ohlcv = EXCHANGE.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["Open time", "Open", "High", "Low", "Close", "Volume"])
    df["Open time"] = pd.to_datetime(df["Open time"], unit="ms")
    df = df.set_index("Open time")
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna().sort_index()
    df.to_csv(file_path)
    return df


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
