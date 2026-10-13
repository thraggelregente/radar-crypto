"""Transactional journal. Failed reads never create an empty account."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time


class Store:
    def __init__(self, path, config):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Estado corrupto")
            db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL, kind TEXT, payload TEXT)")
            row = db.execute("SELECT value FROM state WHERE id=1").fetchone()
            if row is None:
                s = {"schema": 2, "live": config.live, "fingerprint": config.fingerprint(), "created": time.time(),
                     "cash": config.capital, "initial_capital": config.capital, "positions": {}, "dust": {}, "orders": {}, "signals": {},
                     "trades": [], "cooldown": {}, "last_update_id": 0, "high_water": config.capital, "day": "",
                     "day_start": config.capital, "halt": "", "manual_pause": False, "last_eval_bar": 0}
                db.execute("INSERT INTO state VALUES (1, ?)", (json.dumps(s, allow_nan=False),))
            else:
                s = json.loads(row[0])
                if s["schema"] != 2 or s["live"] != config.live:
                    raise RuntimeError("Estado de otra versión o modo")
                if s["fingerprint"] != config.fingerprint():
                    s["halt"] = "Estrategia/configuración cambió; compras bloqueadas, salidas existentes siguen activas"
                    db.execute("UPDATE state SET value=? WHERE id=1", (json.dumps(s, allow_nan=False),))

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def read(self):
        with self.connect() as db:
            return json.loads(db.execute("SELECT value FROM state WHERE id=1").fetchone()[0])

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            s = json.loads(db.execute("SELECT value FROM state WHERE id=1").fetchone()[0])
            yield s
            db.execute("UPDATE state SET value=? WHERE id=1", (json.dumps(s, allow_nan=False),))

    def event(self, kind, payload):
        with self.connect() as db:
            db.execute("INSERT INTO events(ts,kind,payload) VALUES(?,?,?)", (time.time(), kind, json.dumps(payload, allow_nan=False)))

    def legacy_guard(self, path):
        if not Path(path).exists():
            return
        with self.transaction() as s:
            if s.get("legacy_reviewed"):
                return
            try:
                old = json.loads(Path(path).read_text(encoding="utf-8"))
                s["legacy_state"] = old
                if old.get("positions"):
                    s["halt"] = "Posiciones anteriores: revisar en Pionex; no se importan ni venden automáticamente"
            except (ValueError, OSError):
                s["halt"] = "Estado anterior ilegible: revisión manual requerida"
            s["legacy_reviewed"] = True
