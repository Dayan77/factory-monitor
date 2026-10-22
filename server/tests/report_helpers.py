"""Helpers for the reports tests: an in-memory SMTP server (real SMTP dialogue over a local socket, messages kept
in memory) and the scenario C plant (real OEE data) shared by the integration tests."""
from __future__ import annotations

import email
import socketserver
import threading
from email import policy
from typing import Optional


class _Handler(socketserver.StreamRequestHandler):
    def _send(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())

    def handle(self) -> None:
        srv: "MemorySMTP" = self.server.owner          # type: ignore[attr-defined]
        self._send("220 memory-smtp ready")
        mail_from, rcpts = None, []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode(errors="replace").rstrip("\r\n")
            cmd = line[:4].upper()
            if cmd in ("EHLO", "HELO"):
                self._send("250 memory-smtp")
            elif cmd == "MAIL":
                mail_from, rcpts = line[10:].strip(), []
                self._send("250 OK")
            elif cmd == "RCPT":
                addr = line[8:].strip().strip("<>").lower()
                if addr in srv.refuse:
                    self._send("550 5.1.1 mailbox unavailable")
                else:
                    rcpts.append(addr)
                    self._send("250 OK")
            elif cmd == "DATA":
                self._send("354 end with .")
                chunks = []
                while True:
                    ln = self.rfile.readline()
                    if not ln or ln in (b".\r\n", b".\n"):
                        break
                    if ln.startswith(b".."):
                        ln = ln[1:]
                    chunks.append(ln)
                msg = email.message_from_bytes(b"".join(chunks), policy=policy.default)
                with srv.lock:
                    srv.messages.append({"from": mail_from, "to": list(rcpts), "msg": msg})
                self._send("250 OK queued")
            elif cmd in ("RSET", "NOOP"):
                self._send("250 OK")
            elif cmd == "QUIT":
                self._send("221 bye")
                return
            else:
                self._send("502 not implemented")


class MemorySMTP:
    def __init__(self):
        self.messages: list[dict] = []
        self.refuse: set[str] = set()
        self.lock = threading.Lock()
        self._srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        self._srv.daemon_threads = True
        self._srv.owner = self                         # type: ignore[attr-defined]
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def start(self) -> "MemorySMTP":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()

    def to(self, addr: str) -> list:
        return [m["msg"] for m in self.messages if addr.lower() in m["to"]]

    def clear(self) -> None:
        with self.lock:
            self.messages.clear()


def attachments(msg) -> dict[str, bytes]:
    out = {}
    for part in msg.iter_attachments():
        out[part.get_filename()] = part.get_content()
    return out


def body_text(msg) -> str:
    part = msg.get_body(preferencelist=("plain",))
    return part.get_content() if part is not None else ""


def find_link(text: str, marker: str) -> Optional[str]:
    for tok in text.split():
        if marker in tok:
            return tok.strip("<>")
    return None
