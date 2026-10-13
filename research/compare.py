import json
from pathlib import Path

from backtest import fetch_ohlcv, backtest_strategy


OUT = Path(__file__).resolve().parent / "results.json"


def run_all():
    df = fetch_ohlcv("BTC/USDT", "1h", 2000)
    n = len(df)
    split = int(n * 0.8)
    train = df.iloc[:split]
    test = df.iloc[split:]

    strategies = ["ema_cross", "rsi_ema", "pullback"]
    out = {}
    for s in strategies:
        train_res = backtest_strategy(train, s)
        test_res = backtest_strategy(test, s)
        out[s] = {"train": train_res, "test": test_res}

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    # basic human summary
    for s, v in out.items():
        tr = v["test"]["cagr"]
        dd = v["test"]["max_drawdown"]
        print(f"{s}: test CAGR={tr:.2f}%, maxDD={dd:.2f}%")


if __name__ == "__main__":
    run_all()
