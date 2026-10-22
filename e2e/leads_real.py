"""QA: public lead capture against a RUNNING server (default http://localhost:8000) and its Postgres.

Checks: valid lead -> 202 and stored (consent_at, ip_hash only, no raw IP); with no mail config the attempt is
recorded on the row (notify_error) and logged; honeypot -> 202 but NOT stored; consent false/missing and bad
e-mail -> 422; 6th lead from the same client IP within the hour -> 429 while another IP still gets 202;
CORS preflight for https://factoryconnect.tech allowed, unknown origin not.
The client IP is varied with X-Forwarded-For, which the server trusts only from loopback/private peers.

Env: API, PG (psql connection args for `docker exec fm_postgres psql`), BACKEND_LOG (optional path of the
server log to grep for the mail attempt). Exit 0 = all checks passed.
"""
from __future__ import annotations

import os
import random
import subprocess
import sys
import time

import httpx

API = os.getenv("API", "http://localhost:8000")
DB = os.getenv("PG_DB", "factory_monitor")
LOG = os.getenv("BACKEND_LOG")
results: list[bool] = []


def check(ok, name, extra=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   {extra}"))


def sql(q: str) -> str:
    return subprocess.run(["docker", "exec", "fm_postgres", "psql", "-U", "fm_user", "-d", DB, "-tAc", q],
                          capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    stamp = f"{int(time.time())}{random.randint(100, 999)}"
    ip = f"10.{random.randint(1, 250)}.{random.randint(1, 250)}.{random.randint(1, 250)}"
    c = httpx.Client(base_url=API, timeout=30)
    body = lambda tag, **kw: {"name": "QA Lead", "email": f"qa-lead-{stamp}-{tag}@qa-factoryconnect.com.br",
                              "company": "QA", "phone": "", "message": "teste QA", "consent": True,
                              "website": "", "source": "qa-e2e", **kw}
    h = {"X-Forwarded-For": ip}

    r = c.post("/api/public/leads", json=body("ok"), headers=h)
    check(r.status_code == 202 and r.json().get("status") == "accepted", f"valid lead -> {r.status_code}", r.text)
    time.sleep(3)  # e-mail is sent after the response
    row = sql(f"select consent, consent_at is not null, length(ip_hash), ip_hash like '%{ip}%', coalesce(notify_error,''), "
              f"notified_at is null from leads where email='qa-lead-{stamp}-ok@qa-factoryconnect.com.br'")
    check(row != "", "lead stored in Postgres", row)
    if row:
        consent, has_at, hlen, raw_ip, err, not_notified = row.split("|")
        check(consent == "t" and has_at == "t", "consent and consent_at stored", row)
        check(raw_ip == "f" and int(hlen) >= 32, "IP stored only as a keyed hash", row)
        check(err != "" and not_notified == "t", f"no mail config -> attempt recorded on the row (notify_error={err!r})", row)
    if LOG:
        txt = open(LOG, encoding="utf-8", errors="replace").read()
        check("lead" in txt and ("mail" in txt.lower() or "notify" in txt.lower()), "mail attempt logged by the server")

    r = c.post("/api/public/leads", json=body("pot", website="http://spam.example"), headers=h)
    check(r.status_code == 202, f"honeypot -> {r.status_code} (silent accept)", r.text)
    check(sql(f"select count(*) from leads where email='qa-lead-{stamp}-pot@qa-factoryconnect.com.br'") == "0",
          "honeypot lead NOT stored")

    for name, b in (("consent false", body("c0", consent=False)), ("consent missing", {k: v for k, v in body("c1").items() if k != "consent"}),
                    ("bad e-mail", body("bad", email="nao-e-email"))):
        r = c.post("/api/public/leads", json=b, headers=h)
        check(r.status_code == 422, f"{name} -> {r.status_code}", r.text[:150])

    codes = [c.post("/api/public/leads", json=body(f"rl{i}"), headers=h).status_code for i in range(6)]
    # 1 accepted above + these: the limit is 5 per hour per IP -> the 5th..6th of these are 429
    check(codes[:4] == [202] * 4 and codes[4:] == [429, 429], f"rate limit 5/h per IP: {codes}")
    check(int(sql(f"select count(*) from leads where email like 'qa-lead-{stamp}-rl%'")) == 4, "429 leads not stored")
    r = c.post("/api/public/leads", json=body("other"), headers={"X-Forwarded-For": "10.251.251.251" if ip != "10.251.251.251" else "10.252.1.1"})
    check(r.status_code == 202, f"other IP still accepted -> {r.status_code}")

    for origin, allowed in (("https://factoryconnect.tech", True), ("https://www.factoryconnect.tech", True),
                            ("https://evil.example", False)):
        r = c.options("/api/public/leads", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                                    "Access-Control-Request-Headers": "content-type"})
        got = r.headers.get("access-control-allow-origin") == origin
        check(got == allowed, f"CORS preflight {origin} allowed={got}")
    print(f"\n{sum(results)} passed, {len(results) - sum(results)} failed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
