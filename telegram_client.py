"""Telegram notifications and authenticated command ingestion in separate threads."""
import json
import queue
import threading
import time
from urllib.parse import urlencode
from net import APIError, HTTP


class Telegram:
    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store
        self.base = f"https://api.telegram.org/bot{cfg.token}"
        self.http = HTTP()
        self.outbox = queue.Queue(maxsize=100)
        self.inbox = queue.Queue(maxsize=100)
        self.stop = threading.Event()

    def send(self, text, buttons=None):
        if not self.cfg.token or not self.cfg.chat_id:
            return False
        job = {"chat_id": self.cfg.chat_id, "text": text[:4000]}
        if buttons:
            job["reply_markup"] = {"inline_keyboard": buttons}
        try:
            self.outbox.put_nowait(job)
            return True
        except queue.Full:
            return False

    def start(self):
        if self.cfg.token and self.cfg.chat_id:
            for target in (self.writer, self.reader):
                threading.Thread(target=target, daemon=True).start()

    def writer(self):
        while not self.stop.is_set():
            try:
                job = self.outbox.get(timeout=1)
            except queue.Empty:
                continue
            try:
                r = self.http.request("POST", self.base + "/sendMessage", body=json.dumps(job).encode(),
                                      headers={"Content-Type": "application/json"}, timeout=8)
                if not r.get("ok"):
                    raise APIError("Telegram rechazó mensaje")
                self.store.event("telegram_sent", {"message_id": (r.get("result") or {}).get("message_id")})
            except APIError:
                # No silent cooldown; main creates no hour-long suppression for a failed enqueue.
                self.store.event("telegram_failed", {"reason": "no confirmado"})

    def reader(self):
        while not self.stop.is_set():
            try:
                offset = self.store.read()["last_update_id"] + 1
                r = self.http.request("GET", self.base + "/getUpdates?" + urlencode({
                    "offset": offset, "timeout": 5, "allowed_updates": json.dumps(["message", "callback_query"])}), timeout=8)
                if not r.get("ok"):
                    raise APIError("getUpdates rechazado; revisar token/webhook/otro consumidor")
                updates = r.get("result", [])
                if updates:
                    ack = threading.Event()
                    self.inbox.put((updates, ack), timeout=1)
                    while not ack.wait(1) and not self.stop.is_set():
                        pass
            except (APIError, queue.Full):
                self.stop.wait(5)

    def authorized(self, update):
        item = update.get("callback_query") or update.get("message") or {}
        user = str((item.get("from") or {}).get("id", ""))
        message = item.get("message", {}) if "callback_query" in update else item
        chat = str((message.get("chat") or {}).get("id", ""))
        return user in self.cfg.allowed_users and chat == self.cfg.chat_id
