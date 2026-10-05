"""QA test receiver for Sprint 4: a fake Web Push service (HTTPS) and a webhook endpoint (HTTP).

Every request is appended to --log as one JSON line. Push bodies are decrypted with the subscription's
private key (RFC 8291, using the server's own `decrypt`) and the VAPID JWT is checked against the VAPID
public key; webhook bodies are checked against HMAC-SHA256(secret, "<timestamp>.<raw body>").

    python e2e/sprint4/receiver.py --push-port 9443 --cert cert.pem --key key.pem --webhook-port 9180 \
        --ua-key ua_private.pem --auth <b64u> --vapid-public <b64u> --webhook-secret <secret> --log recv.jsonl
"""
import argparse
import base64
import hashlib
import hmac
import json
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402

from src.notifications.webpush import b64u_decode, decrypt  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--push-port", type=int, default=9443)
ap.add_argument("--webhook-port", type=int, default=9180)
ap.add_argument("--cert")
ap.add_argument("--key")
ap.add_argument("--ua-key")
ap.add_argument("--auth")
ap.add_argument("--vapid-public")
ap.add_argument("--webhook-secret")
ap.add_argument("--log")
args = ap.parse_args()
LOCK = threading.Lock()
UA = serialization.load_pem_private_key(Path(args.ua_key).read_bytes(), password=None) if args.ua_key else None


def log(rec):
    with LOCK, open(args.log, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def check_vapid(auth_header: str) -> dict:
    try:
        parts = dict(p.strip().split("=", 1) for p in auth_header[len("vapid "):].split(","))
        jwt, k = parts["t"], parts["k"]
        h, p, s = jwt.split(".")
        pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64u_decode(k))
        raw = b64u_decode(s)
        sig = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        pub.verify(sig, f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256()))
        claims = json.loads(b64u_decode(p))
        return {"ok": True, "k_matches": k == args.vapid_public, "claims": claims}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": repr(e)}


class Push(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        rec = {"kind": "push", "at": time.time(), "path": self.path, "headers": dict(self.headers)}
        rec["vapid"] = check_vapid(self.headers.get("authorization", ""))
        try:
            rec["payload"] = json.loads(decrypt(body, UA, args.auth))
        except Exception as e:  # noqa: BLE001
            rec["decrypt_error"] = repr(e)
        log(rec)
        self.send_response(201)
        self.end_headers()

    def log_message(self, *a):
        pass


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        ts = self.headers.get("x-factoryconnect-timestamp", "")
        sig = self.headers.get("x-factoryconnect-signature", "")
        exp = "sha256=" + hmac.new(args.webhook_secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        log({"kind": "webhook", "at": time.time(), "path": self.path, "headers": dict(self.headers),
             "signature_ok": hmac.compare_digest(exp, sig), "body": json.loads(body or b"null")})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


push = ThreadingHTTPServer(("127.0.0.1", args.push_port), Push)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(args.cert, args.key)
push.socket = ctx.wrap_socket(push.socket, server_side=True)
threading.Thread(target=push.serve_forever, daemon=True).start()
hook = ThreadingHTTPServer(("127.0.0.1", args.webhook_port), Hook)
print(f"receiver: push https://127.0.0.1:{args.push_port} webhook http://127.0.0.1:{args.webhook_port}", flush=True)
hook.serve_forever()
_ = base64
