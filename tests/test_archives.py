"""Archive integrity, venue provenance and small-account feasibility."""
from dataclasses import replace
import hashlib
import io
import json
import os
import time
import unittest
from unittest.mock import patch
import zipfile
from config import Config
from download_binance import decode_archive, month_list, timestamp_ms
from risk import evidence_gate, position_size


class ArchiveTests(unittest.TestCase):
    def archive(self, stamp="1735689600000000"):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("BTCUSDT-1h.csv", f"{stamp},100,102,99,101,5,0,0,0,0,0,0\n")
        raw = buffer.getvalue()
        return raw, (hashlib.sha256(raw).hexdigest() + "  BTCUSDT-1h.zip").encode()

    def test_microseconds_normalized(self):
        self.assertEqual(timestamp_ms("1735689600000000"), 1735689600000)

    def test_milliseconds_preserved(self):
        self.assertEqual(timestamp_ms("1704067200000"), 1704067200000)

    def test_official_checksum_verified_before_decode(self):
        raw, checksum = self.archive()
        with self.assertRaises(ValueError):
            decode_archive(raw, b"0" * 64)

    def test_valid_archive_returns_real_columns(self):
        raw, checksum = self.archive()
        rows, _ = decode_archive(raw, checksum)
        self.assertEqual(rows[0]["close"], "101")
        self.assertEqual(rows[0]["time"], 1735689600000)

    def test_months_cross_year_without_current_month(self):
        # 2026-01-15 UTC: last completed month is December 2025.
        from datetime import datetime, timezone
        now = datetime(2026, 1, 15, tzinfo=timezone.utc).timestamp()
        self.assertEqual(month_list(3, now), ["2025-10", "2025-11", "2025-12"])

    def test_research_mode_never_needs_or_keeps_private_credentials(self):
        with patch.dict(os.environ, {"LIVE_TRADING": "true", "ENTRY_MODE": "semi", "PIONEX_API_KEY": "test",
                                     "PIONEX_API_SECRET": "test", "TELEGRAM_BOT_TOKEN": "test"}, clear=True):
            cfg = Config.from_env(research=True)
        self.assertFalse(cfg.live)
        self.assertEqual(cfg.api_secret, "")
        self.assertEqual(cfg.token, "")

    def test_1422_account_does_not_raise_exposure_to_meet_minimum(self):
        state = {"cash": 14.22, "positions": {}, "dust": {}, "orders": {}, "halt": "", "manual_pause": False}
        signal = {"symbol": "SOL_USDT", "stop_pct": .02}
        cfg = Config(capital=14.22)
        for minimum in (5, 10):
            self.assertEqual(position_size(state, signal, {"amountPrecision": 2, "minAmount": minimum}, cfg, {}), 0)

    def test_binance_proxy_never_authorizes_pionex_live(self):
        cfg = Config(live=True)
        now = time.time()
        evidence = {"source": "Binance public archive", "fingerprint": cfg.fingerprint(), "approved": True,
            "trials": 1, "generated": now, "data_end": now, "history_days": 1000,
            "oos": {"trades": 1000, "expectancy_lower_95": .01, "profit_factor": 2, "max_drawdown": .01},
            "stress": {"net_return": .5}, "positive_folds": 3}
        with patch("risk.Path.read_text", return_value=json.dumps(evidence)):
            self.assertFalse(evidence_gate(cfg)[0])


if __name__ == "__main__":
    unittest.main()
