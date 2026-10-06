"""Sprint 5 — AI assistant (docs/ai_assistant.md), IA-12: orchestrator tested with a scripted model, no network.

Covers: the 15 spec examples produce the expected tool calls / refusals; tools run as the user (RBAC of the route,
forged call → 403 handled); role-scoped tool exposure; tenant isolation in every tool and in conversations; the
verifier rejects an ungrounded number (retry, then the fixed template) and accepts the grounded one; privacy
(IA-10); only GET reaches the API; ≤ 6 calls per turn; rate limit; daily token budget; SSE streaming; provider
selection and the OpenAI / Anthropic adapters (fake clients)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ai_helpers import EXAMPLES, SECRET_EMAIL, SECRET_PHONE, example_script, grounded_answer, seed_ai_tenant
from usage_helpers import api


@pytest.fixture
async def ai(client):
    return await seed_ai_tenant(client)


@pytest.fixture
def scripted():
    """Installs a scripted provider; yields a setter `use(script) -> provider`."""
    from src.services.ai import providers
    holder = {}

    def use(script):
        p = providers.ScriptedProvider(script)
        holder["p"] = p
        providers.set_provider_factory(lambda: p)
        return p
    yield use
    providers.set_provider_factory(None)


@pytest.fixture
def requests_log(monkeypatch):
    """Every internal request the tool layer makes (method, path)."""
    from src.services.ai import tools
    log = []
    orig = tools.ReadOnlyTransport.handle_async_request

    async def spy(self, request):
        log.append((request.method, request.url.path))
        return await orig(self, request)
    monkeypatch.setattr(tools.ReadOnlyTransport, "handle_async_request", spy)
    return log


@pytest.fixture(autouse=True)
def _reset_rate():
    from src.services.ai import assistant
    assistant._rate.clear()
    yield
    assistant._rate.clear()


ROLE_OF = {"manager": "manager", "maintenance_planner": "maintenance_planner", "technician": "technician",
           "production_supervisor": "production_supervisor", "operator": "operator"}


async def ask(client, h, message, context=None, conversation_id=None, expect=200):
    body = {"message": message}
    if context:
        body["context"] = context
    if conversation_id:
        body["conversation_id"] = conversation_id
    return await api(client, "POST", "/api/ai/chat", h, expect, json=body)


@pytest.mark.parametrize("ex", EXAMPLES, ids=lambda e: f"q{e['id']}")
async def test_spec_examples(client, ai, scripted, requests_log, ex):
    ctx = {"plant_id": ai.plant_id, "line_id": ai.line_id, "route": "/factory"}
    p = scripted(example_script(ex, ctx, ai.positions))
    h = ai.h(ROLE_OF[ex["role"]])
    from src.db import AsyncSessionLocal
    from sqlalchemy import func, select
    from src.models import WorkOrder
    async with AsyncSessionLocal() as s:
        wos_before = (await s.execute(select(func.count()).select_from(WorkOrder).where(WorkOrder.tenant_id == ai.id))).scalar()
    r = await ask(client, h, ex["question"], ctx)
    want = ex["answer"]
    assert [c["name"] for c in r["tool_calls"]] == [c["name"] for c in ex["calls"]], r["tool_calls"]
    for c, exp in zip(r["tool_calls"], ex["calls"]):
        assert set(c["args"]) == set(exp["args"])
    assert r["refusal_reason"] == want["refusal_reason"], (r["refusal_reason"], r["reply"])
    assert r["refused"] is (want["refusal_reason"] is not None)
    if want["must_cite"]:
        assert r["verified"] is True and r["answer_source"] == "model", r
        assert r["citations"] and all(c["url"].startswith("/") for c in r["citations"])
        assert all(c["status"] == "ok" for c in r["tool_calls"] if c["name"] not in ("get_predictive_alerts",
                                                                                      "get_condition_trend")), r["tool_calls"]
        assert f"[{r['citations'][0]['id']}]" in r["reply"]
    for term in want.get("must_mention", []):
        assert term in r["reply"]
    offered = p.sessions[0].tools
    for f in want.get("forbidden_tools", []):
        assert f not in offered                                    # IA-4: not even offered to the role
    # IA-11: read only — every internal request is a GET; nothing changed
    assert requests_log and all(m == "GET" for m, _ in requests_log) or not ex["calls"]
    async with AsyncSessionLocal() as s:
        assert (await s.execute(select(func.count()).select_from(WorkOrder).where(WorkOrder.tenant_id == ai.id))).scalar() == wos_before
    if ex["id"] == 14:
        assert "out_of_scope" in r["refusal_reasons"]
    if ex["id"] == 15:
        assert f"position_id={ai.positions['A2']}" in r["reply"]
    if ex["id"] == 11:
        assert "A1" in r["reply"] and "A4" in r["reply"]
        r2 = await ask(client, h, ex["follow_up"]["user"], ctx, conversation_id=r["conversation_id"])
        assert [c["name"] for c in r2["tool_calls"]] == ["search_failures"]
        assert r2["tool_calls"][0]["args"]["position_id"] == ai.positions["A1"]
        assert r2["verified"] and r2["citations"][0]["url"].startswith("/maintenance/failures?f=")
        conv = await api(client, "GET", f"/api/ai/conversations/{r['conversation_id']}", h)
        assert [m["role"] for m in conv["messages"]] == ["user", "assistant", "user", "assistant"]


async def test_role_scoped_tool_exposure(client, ai, scripted):
    p = scripted([{"text": "Olá."}])
    exposed = {}
    for role in ("operator", "technician", "maintenance_planner", "manager", "production_supervisor", "admin"):
        await ask(client, ai.h(role), "Oi")
        exposed[role] = set(p.sessions[-1].tools)
    maint = {"get_component_life", "get_maintenance_due", "search_failures", "get_predictive_alerts",
             "get_condition_trend"}
    kpi = {"get_maintenance_kpis", "get_reliability"}
    assert not (exposed["operator"] & (maint | kpi))
    assert maint <= exposed["technician"] and not (exposed["technician"] & kpi)
    assert (maint | kpi) <= exposed["maintenance_planner"] and (maint | kpi) <= exposed["production_supervisor"]
    assert len(exposed["admin"]) == 14 and exposed["admin"] >= exposed["manager"]
    assert "get_reliability" in p.sessions[0].system and "NÃO tem acesso" in p.sessions[0].system


async def test_forged_tool_call_is_refused_by_the_route(client, ai, scripted):
    """The model calls a tool that was not offered to the operator: the route answers 403 → no_permission."""
    scripted([{"calls": [{"name": "get_reliability", "args": {"group_by": "position"}}]},
              {"text": "Não tenho acesso."}])
    r = await ask(client, ai.h("operator"), "Qual o MTBF?")
    assert r["tool_calls"][0]["status"] == "forbidden" and r["refusal_reason"] == "no_permission"
    assert r["citations"] == []


async def test_verifier_rejects_ungrounded_number_then_template(client, ai, scripted):
    def script(state):
        if state["step"] == 0:
            return {"calls": [{"name": "get_downtime", "args": {"period": "today", "line_id": ai.line_id}}]}
        return {"text": "Hoje houve 37 paradas, total de 912,5 min [1]."}          # invented, twice
    p = scripted(script)
    r = await ask(client, ai.h("manager"), "Quantas paradas hoje?")
    s = p.sessions[0]
    assert s.notes and "912,5" in s.notes[0] and "VERIFICADOR" in s.notes[0]       # one retry with the finding
    assert r["answer_source"] == "template" and r["verified"] is True
    assert "37" not in r["reply"] and "[1]" in r["reply"]
    assert any("modelo fixo" in w for w in r["warnings"])
    # the template is built only from the tool result
    from src.services.ai import verifier
    assert r["citations"][0]["url"].startswith("/stops?line_id=")
    assert verifier.CITE.search(r["reply"])


async def test_verifier_accepts_grounded_retry(client, ai, scripted):
    def script(state):
        if state["step"] == 0:
            return {"calls": [{"name": "get_downtime", "args": {"period": "today", "line_id": ai.line_id}}]}
        if state["step"] == 1:
            return {"text": "Foram 99 paradas hoje [1]."}
        return {"text": grounded_answer(state["results"])}
    p = scripted(script)
    r = await ask(client, ai.h("manager"), "Paradas de hoje?")
    assert p.sessions[0].notes and "'99'" in p.sessions[0].notes[0]
    assert r["answer_source"] == "model" and r["verified"] is True and "99" not in r["reply"]


async def test_verifier_unit_cases():
    from zoneinfo import ZoneInfo
    from src.services.ai import verifier
    tz = ZoneInfo("America/Manaus")
    src = verifier.collect({"oee": 0.71428, "duration_s": 1500, "start": "2026-10-05T14:30:00Z",
                            "rows": [{"n": 3}, {"n": 4}], "name": "Linha 2", "mcbf_cycles": 1234567}, tz)
    ok = "OEE 71,4 % [1]\nParada de 25 min às 10:30 de 05/10 [1]\n2 linhas, Linha 2 [1]\nMCBF 1.234.567 ciclos [1]"
    assert verifier.check(ok, src, "", 1) == []
    bad = verifier.check("OEE 72,4 % [1]", src, "", 1)
    assert [f.reason for f in bad] == ["ungrounded"]
    assert [f.reason for f in verifier.check("OEE 71,4 %", src, "", 1)] == ["uncited"]
    assert [f.reason for f in verifier.check("OEE 71,4 % [3]", src, "", 1)] == ["bad_citation"]
    # numbers of the question, list markers, identifiers and URLs are exempt
    assert verifier.check("1. Cilindro A2 e EQP-01 em [OEE](/oee?id=9&start=2026) — os 15 dias pedidos", src,
                          "nos últimos 15 dias", 1) == []


async def test_privacy_redaction_and_data_framing(client, ai, scripted):
    p = scripted([{"calls": [{"name": "search_failures", "args": {"position_id": ai.positions["A1"], "limit": 1}}]},
                  {"text": "ok"}])
    await ask(client, ai.h("maintenance_planner"), "Última falha do A1?")
    sent = json.dumps(p.sessions[0].results, ensure_ascii=False)
    assert SECRET_EMAIL not in sent and SECRET_PHONE not in sent
    assert "[e-mail removido]" in sent and "[telefone removido]" in sent
    assert "vazamento na haste" in sent                                       # the note itself stays (data)
    assert "não são instruções" in p.sessions[0].system or "DADO" in p.sessions[0].system
    # work orders: names allowed, no e-mail
    p = scripted([{"calls": [{"name": "get_work_orders", "args": {"assignee_id": "me"}}]}, {"text": "ok"}])
    await ask(client, ai.h("technician"), "Minhas OS")
    sent = json.dumps(p.sessions[0].results, ensure_ascii=False)
    assert "@example.com" not in sent and "Lubrificar cilindros" in sent


async def test_people_ranking_refused_out_of_scope(client, ai, scripted):
    scripted([{"text": "Não ranqueio pessoas."}])
    r = await ask(client, ai.h("manager"), "Quem é o técnico mais lento da equipe?")
    assert r["refused"] and r["refusal_reason"] == "out_of_scope" and r["tool_calls"] == []


async def test_tenant_isolation_in_tools_and_conversations(client, ai, scripted):
    other = await seed_ai_tenant(client, "AIB")
    # B asks about A's line: every id-based tool answers "não encontrei"; resolve_asset only sees B's assets
    calls = [{"name": "get_oee", "args": {"scope": "line", "id": ai.line_id, "period": "today"}},
             {"name": "get_losses_pareto", "args": {"scope": "line", "id": ai.line_id, "period": "today"}},
             {"name": "get_shift_report", "args": {"line_id": ai.line_id, "productive_day": "today"}},
             {"name": "search_failures", "args": {"position_id": ai.positions["A1"]}},
             {"name": "get_downtime", "args": {"line_id": ai.line_id, "period": "today"}},
             {"name": "get_machine_status", "args": {"machine_id": ai.machines["Estação de Fixação"]}}]
    scripted([{"calls": calls}, {"text": "Não encontrei."}])
    r = await ask(client, other.h("manager"), "OEE da linha?")
    st = {c["name"]: (c["status"], c["rows"]) for c in r["tool_calls"]}
    assert st["get_oee"][0] == st["get_losses_pareto"][0] == st["get_shift_report"][0] == "not_found"
    assert st["get_machine_status"][0] == "not_found"
    assert st["search_failures"] == ("ok", 0) and st["get_downtime"] == ("ok", 0)   # filters by tenant: nothing
    p = scripted([{"calls": [{"name": "resolve_asset", "args": {"query": "Cilindro", "kind": "position"}}]},
                  {"text": "ok"}])
    await ask(client, other.h("manager"), "cilindro")
    ids = {mm["id"] for mm in p.sessions[0].results[0]["data"]["matches"]}
    assert ids == set(other.positions.values()) and not ids & set(ai.positions.values())
    # conversations are private to the user and the tenant
    scripted([{"text": "Olá."}])
    r = await ask(client, ai.h("manager"), "Oi")
    cid = r["conversation_id"]
    await api(client, "GET", f"/api/ai/conversations/{cid}", other.h("manager"), 404)
    await api(client, "GET", f"/api/ai/conversations/{cid}", ai.h("maintenance_planner"), 404)
    await api(client, "DELETE", f"/api/ai/conversations/{cid}", other.h("admin"), 404)
    await ask(client, other.h("manager"), "Oi", conversation_id=cid, expect=404)
    assert cid in [c["id"] for c in await api(client, "GET", "/api/ai/conversations", ai.h("manager"))]
    assert cid not in [c["id"] for c in await api(client, "GET", "/api/ai/conversations", other.h("manager"))]
    # audit: admin of A sees A's tool calls only
    aud_b = await api(client, "GET", "/api/ai/audit", other.h("admin"))
    assert aud_b and all(a["user_id"] in {u["id"] for u in other.users.values()} for a in aud_b)
    await api(client, "GET", "/api/ai/audit", ai.h("manager"), 403)
    await api(client, "DELETE", f"/api/ai/conversations/{cid}", ai.h("manager"), 204)
    await api(client, "GET", f"/api/ai/conversations/{cid}", ai.h("manager"), 404)


async def test_tool_call_limit_per_turn(client, ai, scripted):
    many = [{"name": "get_machine_status", "args": {}} for _ in range(8)]
    scripted([{"calls": many}, {"text": "ok"}])
    r = await ask(client, ai.h("manager"), "status")
    assert len(r["tool_calls"]) == 6


async def test_rate_limit_and_daily_budget(client, ai, scripted, monkeypatch):
    from src.config import settings
    scripted([{"text": "Olá.", "input_tokens": 600, "output_tokens": 500}])
    monkeypatch.setattr(settings, "ai_rate_limit_per_min", 2)
    await ask(client, ai.h("operator"), "a")
    await ask(client, ai.h("operator"), "b")
    await ask(client, ai.h("operator"), "c", expect=429)
    monkeypatch.setattr(settings, "ai_rate_limit_per_min", 100)
    monkeypatch.setattr(settings, "ai_daily_token_budget", 1000)        # 2 × 1100 already used today
    r = await ask(client, ai.h("manager"), "d")
    assert r["refused"] and r["refusal_reason"] == "budget_exceeded" and "Limite diário" in r["reply"]
    st = await api(client, "GET", "/api/ai/status", ai.h("manager"))
    assert st["tokens_used_today"] >= 2200


async def test_sse_stream(client, ai, scripted):
    scripted([{"calls": [{"name": "get_machine_status", "args": {"plant_id": ai.plant_id}}]},
              {"text": "Há máquinas paradas."}])
    events = []
    async with client.stream("POST", "/api/ai/chat/stream", headers=ai.h("manager"),
                             json={"message": "O que está parado?"}) as resp:
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
        ev = None
        async for line in resp.aiter_lines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                events.append((ev, json.loads(line[6:])))
    kinds = [k for k, _ in events]
    assert kinds[0] == "start" and "tool_call" in kinds and "tool_result" in kinds and "verifying" in kinds
    assert kinds[-1] == "done" and "delta" in kinds
    assert "".join(d["text"] for k, d in events if k == "delta") == events[-1][1]["reply"]
    assert events[-1][1]["tool_calls"][0]["name"] == "get_machine_status"


async def test_no_provider_configured(client, ai, monkeypatch):
    from src.config import settings
    from src.services.ai import providers
    providers.set_provider_factory(None)
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    r = await ask(client, ai.h("manager"), "OEE?")
    assert "não está configurado" in r["reply"] and r["verified"] is False and r["answer_source"] == "system"


def test_provider_selection(monkeypatch):
    from src.config import settings
    from src.services.ai import providers
    for prov, oa, an, want in [("", "k", "", "openai"), ("", "", "k", "anthropic"), ("", "k", "k", "anthropic"),
                               ("openai", "k", "k", "openai"), ("anthropic", "k", "", None), ("", "", "", None)]:
        monkeypatch.setattr(settings, "ai_provider", prov)
        monkeypatch.setattr(settings, "openai_api_key", oa)
        monkeypatch.setattr(settings, "anthropic_api_key", an)
        assert providers.configured_provider() == want, (prov, oa, an)
    monkeypatch.setattr(settings, "ai_provider", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "k")
    monkeypatch.setattr(settings, "ai_model", "")
    p = providers.get_provider()
    assert p.name == "anthropic" and p.model == "claude-opus-5-5"


async def test_anthropic_adapter_tool_loop_with_fake_client(monkeypatch):
    """Request shape of the Claude adapter: tools with JSON schema + eager streaming, no sampling params, low
    effort, server-side fallback; tool results go back in one user message; assistant blocks echoed."""
    from src.config import settings
    from src.services.ai import providers
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    p = providers.AnthropicProvider("claude-opus-5-5")
    seen = []

    class Block(SimpleNamespace):
        def model_dump(self, **kw):
            return {k: v for k, v in self.__dict__.items()}

    replies = [SimpleNamespace(content=[Block(type="thinking", thinking="", signature="sig"),
                                        Block(type="tool_use", id="tu_1", name="get_oee",
                                              input={"scope": "line", "id": "x", "period": "today"})],
                               stop_reason="tool_use", model="claude-opus-5-5",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5)),
               SimpleNamespace(content=[Block(type="text", text="OEE 50,0 % [1]")], stop_reason="end_turn",
                               model="claude-opus-5-5", usage=SimpleNamespace(input_tokens=20, output_tokens=7))]

    class Stream:
        def __init__(self, kw):
            seen.append(json.loads(json.dumps(kw, default=str)))
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get_final_message(self):
            return replies[len(seen) - 1]

    p.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: Stream(kw))))
    s = p.start("sys", [("user", "OEE?")], [{"name": "get_oee", "description": "d",
                                              "schema": {"type": "object", "properties": {}}}])
    t1 = await s.step()
    assert t1.stop == "tool_use" and t1.calls[0].name == "get_oee" and t1.calls[0].args["period"] == "today"
    s.add_tool_results([("tu_1", '{"status": "ok"}', False)])
    t2 = await s.step()
    assert t2.text == "OEE 50,0 % [1]" and not t2.calls
    k = seen[0]
    assert k["model"] == "claude-opus-5-5" and "temperature" not in k and k["output_config"] == {"effort": "low"}
    assert k["fallbacks"] == "default" and k["betas"] == ["server-side-fallback-2026-07-01"]
    assert k["tools"][0]["eager_input_streaming"] is True and k["tools"][0]["input_schema"]["type"] == "object"
    assert "tool_choice" not in k
    m2 = seen[1]["messages"]
    assert m2[1]["role"] == "assistant" and m2[1]["content"][0]["type"] == "thinking"     # echoed unchanged
    assert m2[2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1",
                                                  "content": '{"status": "ok"}'}]}


async def test_openai_adapter_function_calling_with_fake_client(monkeypatch):
    from src.config import settings
    from src.services.ai import providers
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    p = providers.OpenAIProvider("gpt-4o")
    seen = []
    tc = SimpleNamespace(id="c1", function=SimpleNamespace(name="get_oee", arguments='{"scope":"line","id":"x"}'))
    outs = [SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tc]),
                                                     finish_reason="tool_calls")], model="gpt-4o",
                            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2)),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None),
                                                     finish_reason="stop")], model="gpt-4o",
                            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2))]

    async def create(**kw):
        seen.append(json.loads(json.dumps(kw)))
        return outs[len(seen) - 1]
    p.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    s = p.start("sys", [("user", "OEE?")], [{"name": "get_oee", "description": "d", "schema": {"type": "object"}}])
    t1 = await s.step()
    assert t1.calls[0].args == {"scope": "line", "id": "x"}
    s.add_tool_results([("c1", "{}", False)])
    t2 = await s.step()
    assert t2.text == "ok"
    assert seen[0]["temperature"] <= 0.2 and seen[0]["tools"][0]["function"]["name"] == "get_oee"
    assert seen[1]["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "{}"}


async def test_oee_route_shift_name_and_compare_to(client, ai):
    """API extensions used by get_oee (ai_assistant.md §4): group_by=shift_name and compare_to=previous_period."""
    h = ai.h("manager")
    o = await api(client, "GET", "/api/oee", h, params={"scope": "line", "id": ai.line_id, "group_by": "shift_name",
                                                         "compare_to": "previous_period"})
    assert o["granularity"] == "shift_name" and o["previous"] is not None and set(o["delta"]) >= {"oee", "availability"}
    labels = [pt["label"] for pt in o["points"]]
    assert len(labels) == len(set(labels)) and set(labels) <= {"Turno A", "Turno B", "Turno C"}
