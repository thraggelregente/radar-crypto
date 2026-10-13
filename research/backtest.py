import os
import json
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd
import vectorbt as vbt


DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def safe_float(value, default=0.0):
    """Convert a stat to float and guard NaN/Inf/None values."""
    try:
        val = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(val):
        return default
    return val


def fetch_ohlcv(symbol: str = "BTC/USDT", timeframe: str = "1h", limit: int = 2000):
    file_path = DATA_DIR / f"{symbol.replace('/', '_').lower()}_{timeframe}.csv"
    if os.getenv("BACKTEST_FORCE_LOCAL", "0") == "1" and file_path.exists():
        print(f"[fetch_ohlcv] BACKTEST_FORCE_LOCAL=1 -> using local file {file_path}")
        return pd.read_csv(file_path, index_col=0, parse_dates=True)

    if file_path.exists():
        print(f"[fetch_ohlcv] Cached file {file_path} exists; using it.")
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
            exchange_cls = getattr(ccxt, ex_id)
            ex_opts = {"enableRateLimit": True}
            ex_opts.update(params or {})
            exchange = exchange_cls(ex_opts)
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

            df = pd.DataFrame(ohlcv, columns=["Open time", "Open", "High", "Low", "Close", "Volume"])
            df["Open time"] = pd.to_datetime(df["Open time"], unit="ms")
            df = df.set_index("Open time")
            for col in ["Open", "High", "Low", "Close", "Volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna().sort_index()
            df.to_csv(file_path)
            print(f"[fetch_ohlcv] Success from {name}; saved to {file_path}")
            return df
        except Exception as e:
            print(f"[fetch_ohlcv] {name} failed: {type(e).__name__}: {e}")
            last_exc = e

    if file_path.exists():
        return pd.read_csv(file_path, index_col=0, parse_dates=True)

    raise last_exc or RuntimeError("Failed to fetch OHLCV from any source and no local file found.")


def ema(series: pd.Series, span: int):
    return series.ewm(span=span, adjust=False).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    hl = high - low
    hc = (high - close.shift(1)).abs()
    lc = (low - close.shift(1)).abs()
    true_range = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def trend_breakout_strategy(df: pd.DataFrame):
    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    ema_fast = ema(close, 20)
    ema_slow = ema(close, 50)
    atr14 = atr(high, low, close, period=14)
    atr_med = atr14.rolling(50, min_periods=20).median()

    trend_long = ema_fast > ema_slow
    trend_short = ema_fast < ema_slow

    rolling_high = close.rolling(20, min_periods=10).max()
    rolling_low = close.rolling(20, min_periods=10).min()

    long_entry = (
        (close > rolling_high.shift(1))
        & trend_long
        & (atr14 > atr_med * 0.9)
    )

    short_entry = (
        (close < rolling_low.shift(1))
        & trend_short
        & (atr14 > atr_med * 0.9)
    )

    long_exit = ema_fast < ema_slow
    short_exit = ema_fast > ema_slow

    entries = np.where(long_entry.to_numpy(), 1, np.where(short_entry.to_numpy(), -1, 0))
    exits = np.where(long_exit.to_numpy(), 1, np.where(short_exit.to_numpy(), -1, 0))

    pf = vbt.Portfolio.from_signals(
        close,
        entries=entries,
        exits=exits,
        init_cash=10000,
        fees=0.001,
        slippage=0.0005,
        freq="1h",
    )

    stats = pf.stats()
    trade_count = int(stats.get("Total Trades", 0) or 0)
    total_return = safe_float(stats.get("Total Return [%]", 0.0))
    cagr = safe_float(stats.get("CAGR [%]", 0.0))
    sharpe = safe_float(stats.get("Sharpe Ratio", 0.0))
    max_dd = safe_float(stats.get("Max Drawdown [%]", 0.0))
    win_rate = safe_float(stats.get("Win Rate [%]", 0.0))
    profit_factor = safe_float(stats.get("Profit Factor", 0.0))

    if trade_count == 0:
        sharpe = 0.0
        max_dd = 0.0
        win_rate = 0.0
        profit_factor = 0.0

    return {
        "strategy": "trend_breakout",
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "trades": trade_count,
        "profit_factor": profit_factor,
    }


def walkforward_backtest(df: pd.DataFrame, strategy_fn, folds: int = 4):
    n = len(df)
    if n < 500:
        raise ValueError(f"Not enough rows for walk-forward validation: {n} < 500")

    test_size = max(200, n // 6)
    test_size = min(test_size, n // 3)
    if n - test_size <= 0:
        raise ValueError("Not enough data to build a test window")

    base_start = int(n * 0.35)
    end_anchor = n - test_size
    if end_anchor <= base_start:
        end_anchor = n - test_size

    step = max(1, (end_anchor - base_start) // max(1, folds - 1))
    folds_out = []

    for i in range(folds):
        train_end = min(base_start + i * step, end_anchor)
        test_start = train_end
        test_end = min(test_start + test_size, n)
        if test_end <= test_start:
            continue

        train = df.iloc[:train_end]
        test = df.iloc[test_start:test_end]
        train_res = strategy_fn(train)
        test_res = strategy_fn(test)

        folds_out.append({
            "fold": i + 1,
            "train_size": len(train),
            "test_size": len(test),
            "train": train_res,
            "test": test_res,
        })

    if not folds_out:
        raise ValueError("No valid walk-forward windows could be created from the given data")

    return folds_out


def summarize_walkforward(results):
    total_returns = [item["test"]["total_return"] for item in results]
    sharpe_vals = [item["test"]["sharpe"] for item in results]
    drawdowns = [item["test"]["max_drawdown"] for item in results]
    win_rates = [item["test"]["win_rate"] for item in results]
    profit_factors = [item["test"]["profit_factor"] for item in results]

    positive_windows = sum(1 for r in total_returns if r > 0)
    avg_return = sum(total_returns) / len(total_returns) if total_returns else 0.0
    avg_sharpe = sum(sharpe_vals) / len(sharpe_vals) if sharpe_vals else 0.0
    avg_drawdown = sum(drawdowns) / len(drawdowns) if drawdowns else 0.0
    avg_win = sum(win_rates) / len(win_rates) if win_rates else 0.0
    avg_profit_factor = sum(profit_factors) / len(profit_factors) if profit_factors else 0.0

    return {
        "windows": len(results),
        "positive_windows": positive_windows,
        "negative_windows": len(results) - positive_windows,
        "avg_total_return": avg_return,
        "avg_sharpe": avg_sharpe,
        "avg_max_drawdown": avg_drawdown,
        "avg_win_rate": avg_win,
        "avg_profit_factor": avg_profit_factor,
    }


def main():
    df = fetch_ohlcv("BTC/USDT", "1h", 2000)
    results = walkforward_backtest(df, trend_breakout_strategy, folds=4)
    summary = summarize_walkforward(results)
    payload = {"trend_breakout": {"walkforward": results, "summary": summary}}

    output_path = Path(__file__).resolve().parent / "results_v2.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
