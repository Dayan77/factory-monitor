"""Runs the real server (uvicorn, real HTTP) with a QA scripted model injected through
`providers.set_provider_factory` — the hook the server keeps for tests/browser checks. Development only.

Questions starting with a QA tag drive deliberate model behaviour; everything else goes to the server's own
demo script (AI_PROVIDER=scripted):

* ``QA-TOOL <name> <json-args>``  step 0 calls that tool even if it was NOT offered to the user's role (a model
  that ignores the tool list); step 1 answers quoting the first number of each result with its citation.
* ``QA-INVENT``  calls get_machine_status, then ALWAYS answers with a number that is not in any tool result
  ("OEE de 87,3 % [1]") — the verifier must reject it, retry once, then fall back to the template.
* ``QA-LIST``  answers with the names of the tools the server offered to the model for this user (no tool call).
* ``QA-GROUNDED <name> <json-args>``  same as QA-TOOL but answers only with numbers taken from the results.

    cd server && set -a && . ../.env && set +a && POSTGRES_DB=... ../.venv/bin/python ../e2e/sprint5/ai_qa_server.py 8161
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.getcwd())          # server/

import uvicorn  # noqa: E402

from src.services.ai import providers  # noqa: E402


def _numbers(data, out):
    if isinstance(data, bool) or data is None:
        return out
    if isinstance(data, (int, float)):
        out.append(data)
    elif isinstance(data, dict):
        for v in data.values():
            _numbers(v, out)
    elif isinstance(data, list):
        for v in data:
            _numbers(v, out)
    return out


def _fmt(x) -> str:
    return f"{int(x):,}".replace(",", ".") if float(x).is_integer() else f"{x:.2f}".replace(".", ",")


def qa_script(state: dict) -> dict:
    q = state["question"].strip()
    m = re.match(r"QA-(TOOL|GROUNDED)\s+(\w+)\s*(\{.*\})?\s*$", q, re.S)
    if m:
        if state["step"] == 0 and state["allow_tools"]:
            return {"calls": [{"name": m.group(2), "args": json.loads(m.group(3) or "{}")}]}
        lines = ["Resultado QA:"]
        for r in state["results"]:
            if r.get("status") != "ok" or not r.get("citation"):
                lines.append(f"- sem dados ({r.get('status')}: {r.get('message')})")
                continue
            nums = _numbers(r.get("data"), [])
            lines.append(f"- valor {_fmt(nums[0])} [{r['citation']}]" if nums else f"- nenhum registro [{r['citation']}]")
        return {"text": "\n".join(lines)}
    if q.startswith("QA-LIST"):
        return {"text": "ferramentas: " + ", ".join(sorted(state["tools"]))}
    if q.startswith("QA-INVENT"):
        if state["step"] == 0 and state["allow_tools"]:
            return {"calls": [{"name": "get_machine_status", "args": {}}]}
        return {"text": "O OEE da linha foi de 87,3 % ontem [1]."}
    return providers.demo_script(state)


providers.set_provider_factory(lambda: providers.ScriptedProvider(qa_script, model="qa-scripted"))

if __name__ == "__main__":
    from src.main import app
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8161, log_level="warning")
