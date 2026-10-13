"""Bounded HTTP; ambiguous writes are never retried."""
import json
import socket
import threading
import time
import urllib.error
import urllib.request


class APIError(RuntimeError):
    def __init__(self, message, *, ambiguous=False, code="", http_status=0):
        super().__init__(message)
        self.ambiguous, self.code, self.http_status = ambiguous, code, http_status


class HTTP:
    def __init__(self):
        self.lock = threading.Lock()
        self.next_request = self.blocked_until = 0.0

    def request(self, method, url, *, body=None, headers=None, weight=1, timeout=5):
        with self.lock:
            now = time.monotonic()
            if now < self.blocked_until:
                raise APIError("API en enfriamiento", code="RATE_LIMIT")
            if self.next_request > now:
                time.sleep(self.next_request - now)
            self.next_request = time.monotonic() + max(1, weight) / 5.0
        req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        write = method != "GET"
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read(2_000_000)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self.blocked_until = time.monotonic() + 65
            try:
                data = json.loads(e.read(10000))
            except (ValueError, OSError):
                data = {}
            raise APIError(str(data.get("message", f"HTTP {e.code}"))[:200],
                           ambiguous=write and (e.code >= 500 or e.code in {408, 429}),
                           code=str(data.get("code", "")), http_status=e.code) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise APIError("Conexión no confirmada", ambiguous=write, code="TRANSPORT") from None
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise APIError("Respuesta JSON inválida", ambiguous=write, code="INVALID_RESPONSE") from None
        if not isinstance(data, dict):
            raise APIError("Respuesta inesperada", ambiguous=write, code="INVALID_RESPONSE")
        return data
