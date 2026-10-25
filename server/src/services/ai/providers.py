"""Model providers of the assistant (pluggable: AI_PROVIDER = openai | anthropic, model = AI_MODEL).

A provider opens a *session* for one chat turn: system prompt + prior conversation (plain text) + the tools the
user's role may use. `step()` runs one model call and returns text and/or tool calls; tool results go back with
`add_tool_results()`. The provider keeps its own native message history, so provider-specific blocks (Claude's
thinking / fallback blocks, OpenAI tool_calls) are echoed back unchanged within the turn.

* OpenAI — Chat Completions function calling (`openai` SDK), temperature = AI_TEMPERATURE (≤ 0.2).
* Anthropic — Messages API tool use (official `anthropic` SDK, streamed with `get_final_message()`), default model
  `claude-opus-5-5`, adaptive thinking (the model default), `output_config.effort = "low"` (chat), server-side
  refusal fallbacks (`fallbacks="default"`). Current Claude models reject sampling parameters, so `temperature`
  is not sent to them (determinism comes from low effort + the verifier); it is sent only to the older models
  that still accept it.
* Scripted — deterministic fake for tests and the browser check (no network).

Default provider: whichever key is configured, ANTHROPIC_API_KEY preferred when both are.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from ...config import settings

ANTHROPIC_DEFAULT_MODEL = "claude-opus-5-5"
OPENAI_DEFAULT_MODEL = "gpt-4o"
# Claude models that still accept `temperature` (everything newer rejects sampling parameters with a 400)
ANTHROPIC_SAMPLING_MODELS = ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-6", "claude-sonnet-4-5",
                             "claude-opus-4-5")


class ProviderUnavailable(RuntimeError):
    pass


@dataclass
class ToolCallReq:
    id: str
    name: str
    args: Any
    raw_error: Optional[str] = None      # arguments that could not be parsed


@dataclass
class ModelTurn:
    text: str = ""
    calls: list[ToolCallReq] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    stop: str = "end"                    # end | tool_use | max_tokens | refusal
    model: str = ""


class Session(Protocol):
    async def step(self, allow_tools: bool = True) -> ModelTurn: ...
    def add_tool_results(self, results: list[tuple[str, str, bool]]) -> None: ...
    def add_user_note(self, text: str) -> None: ...


class Provider(Protocol):
    name: str
    model: str

    def start(self, system: str, history: list[tuple[str, str]], tools: list[dict]) -> Session: ...


# ── selection ────────────────────────────────────────────────────────────────

def configured_provider() -> Optional[str]:
    want = (settings.ai_provider or "").strip().lower()
    if want == "scripted":
        return None if settings.app_env.lower() in ("production", "prod") else "scripted"
    if want in ("openai", "anthropic"):
        key = settings.openai_api_key if want == "openai" else settings.anthropic_api_key
        return want if key else None
    if settings.anthropic_api_key:
        return "anthropic"
    if settings.openai_api_key:
        return "openai"
    return None


_override: Optional[Callable[[], "Provider"]] = None


def set_provider_factory(factory: Optional[Callable[[], "Provider"]]) -> None:
    """Tests / the scripted browser check inject a provider here."""
    global _override
    _override = factory


def get_provider() -> "Provider":
    if _override is not None:
        return _override()
    which = configured_provider()
    if which == "anthropic":
        return AnthropicProvider(settings.ai_model or ANTHROPIC_DEFAULT_MODEL)
    if which == "openai":
        return OpenAIProvider(settings.ai_model or OPENAI_DEFAULT_MODEL)
    want = (settings.ai_provider or "").strip().lower()
    if want == "scripted":
        if settings.app_env.lower() in ("production", "prod"):
            raise ProviderUnavailable("AI_PROVIDER=scripted é só para desenvolvimento/testes.")
        return ScriptedProvider(demo_script, model="scripted-demo")
    if want in ("openai", "anthropic"):
        raise ProviderUnavailable(f"AI_PROVIDER={want}, mas a chave {'OPENAI' if want == 'openai' else 'ANTHROPIC'}"
                                  "_API_KEY não está definida no .env.")
    raise ProviderUnavailable("O módulo de IA não está configurado. Defina ANTHROPIC_API_KEY ou OPENAI_API_KEY "
                              "(e, opcionalmente, AI_PROVIDER e AI_MODEL) no arquivo .env para ativar o assistente.")


# ── OpenAI ───────────────────────────────────────────────────────────────────

class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str):
        from openai import AsyncOpenAI
        self.model = model
        self.client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.ai_turn_timeout_s)

    def start(self, system, history, tools):
        return _OpenAISession(self, system, history, tools)


class _OpenAISession:
    def __init__(self, p: OpenAIProvider, system: str, history, tools):
        self.p = p
        self.messages: list[dict] = [{"role": "system", "content": system}]
        self.messages += [{"role": r, "content": t} for r, t in history]
        self.tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                         "parameters": t["schema"]}} for t in tools]

    async def step(self, allow_tools: bool = True) -> ModelTurn:
        kw: dict = {"model": self.p.model, "messages": self.messages,
                    "temperature": min(settings.ai_temperature, 0.2),
                    "max_completion_tokens": settings.ai_max_output_tokens}
        if allow_tools and self.tools:
            kw["tools"] = self.tools
        r = await self.p.client.chat.completions.create(**kw)
        msg = r.choices[0].message
        turn = ModelTurn(text=msg.content or "", model=r.model or self.p.model,
                         input_tokens=getattr(r.usage, "prompt_tokens", 0) or 0,
                         output_tokens=getattr(r.usage, "completion_tokens", 0) or 0)
        assistant: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant["tool_calls"] = [{"id": c.id, "type": "function",
                                        "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                       for c in msg.tool_calls]
            for c in msg.tool_calls:
                try:
                    args, err = json.loads(c.function.arguments or "{}"), None
                except ValueError:
                    args, err = {}, "argumentos não são JSON válido"
                turn.calls.append(ToolCallReq(c.id, c.function.name, args, err))
            turn.stop = "tool_use"
        elif r.choices[0].finish_reason == "length":
            turn.stop = "max_tokens"
        self.messages.append(assistant)
        return turn

    def add_tool_results(self, results):
        for call_id, content, _err in results:
            self.messages.append({"role": "tool", "tool_call_id": call_id, "content": content})

    def add_user_note(self, text):
        self.messages.append({"role": "user", "content": text})


# ── Anthropic ────────────────────────────────────────────────────────────────

class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str):
        import anthropic
        self.model = model
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=settings.ai_turn_timeout_s,
                                               max_retries=1)

    def start(self, system, history, tools):
        return _AnthropicSession(self, system, history, tools)


class _AnthropicSession:
    def __init__(self, p: AnthropicProvider, system: str, history, tools):
        self.p = p
        self.system = system
        self.messages: list[dict] = []
        for role, text in history:                         # alternate user/assistant, merge repeats
            if self.messages and self.messages[-1]["role"] == role:
                self.messages[-1]["content"] += "\n\n" + text
            else:
                self.messages.append({"role": role, "content": text})
        # eager input streaming: inputs stream as generated; the tool layer validates every argument itself
        self.tools = [{"name": t["name"], "description": t["description"], "input_schema": t["schema"],
                       "eager_input_streaming": True} for t in tools]
        self._pending_results: list[dict] = []

    def _kwargs(self, allow_tools: bool) -> dict:
        kw: dict = {"model": self.p.model, "max_tokens": settings.ai_max_output_tokens, "system": self.system,
                    "messages": self.messages, "output_config": {"effort": "low"}}
        if allow_tools and self.tools:
            kw["tools"] = self.tools                     # tool_choice stays "auto" (forced choice 400s)
        if self.p.model.startswith(ANTHROPIC_SAMPLING_MODELS):
            kw["extra_body"] = {"temperature": min(settings.ai_temperature, 0.2)}
            kw.pop("output_config")
        else:
            kw["betas"] = ["server-side-fallback-2026-07-01"]
            kw["fallbacks"] = "default"
        return kw

    async def step(self, allow_tools: bool = True) -> ModelTurn:
        import anthropic
        if self._pending_results:
            self.messages.append({"role": "user", "content": self._pending_results})
            self._pending_results = []
        kw = self._kwargs(allow_tools)
        try:
            if "betas" in kw:
                async with self.p.client.beta.messages.stream(**kw) as stream:
                    msg = await stream.get_final_message()
            else:
                async with self.p.client.messages.stream(**kw) as stream:
                    msg = await stream.get_final_message()
        except ValueError:
            # tool-input JSON the SDK could not parse at all (eager streaming): re-issue once without tools
            if not allow_tools:
                raise
            return await self.step(allow_tools=False)
        except anthropic.APIStatusError:
            raise
        turn = ModelTurn(model=getattr(msg, "model", self.p.model),
                         input_tokens=getattr(msg.usage, "input_tokens", 0) or 0,
                         output_tokens=getattr(msg.usage, "output_tokens", 0) or 0)
        texts = []
        for b in msg.content:
            if b.type == "text":
                texts.append(b.text)
            elif b.type == "tool_use":
                args = b.input if isinstance(b.input, dict) else {}
                turn.calls.append(ToolCallReq(b.id, b.name, args, None if isinstance(b.input, dict)
                                              else "argumentos inválidos"))
        turn.text = "".join(texts)
        if msg.stop_reason == "refusal":
            turn.stop, turn.calls = "refusal", []
        elif msg.stop_reason == "max_tokens":
            turn.stop = "max_tokens"
            turn.calls = []                               # a truncated tool input is never run
        elif turn.calls:
            turn.stop = "tool_use"
        # echo the assistant content back unchanged (thinking / fallback blocks included) within the turn
        self.messages.append({"role": "assistant", "content": _echo_blocks(msg.content)})
        return turn

    def add_tool_results(self, results):
        # all results of one assistant message go back in a single user message
        self._pending_results += [{"type": "tool_result", "tool_use_id": cid, "content": content,
                                   **({"is_error": True} if err else {})} for cid, content, err in results]

    def add_user_note(self, text):
        if self._pending_results:
            self._pending_results.append({"type": "text", "text": text})
        else:
            self.messages.append({"role": "user", "content": text})


def _echo_blocks(content) -> list[dict]:
    """Assistant blocks to send back. After a server-side fallback, model-internal blocks (thinking, tool_use, …)
    that precede the last `fallback` marker are dropped and the marker itself is dropped (an audit marker);
    text blocks and everything after the boundary are echoed unchanged."""
    blocks = list(content)
    last_fb = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
    out = []
    for i, b in enumerate(blocks):
        if b.type == "fallback" or (i < last_fb and b.type != "text"):
            continue
        out.append(b.model_dump(exclude_none=True, by_alias=True))
    return out


# ── scripted (tests, browser check) ──────────────────────────────────────────

class ScriptedProvider:
    """Deterministic provider. `script(state) -> {"calls": [{"name", "args"}], "text": str}` is called once per
    model step; `state` = {"step", "question", "results" (list of model-visible tool results so far, in order),
    "notes" (verifier notes), "tools" (names offered), "allow_tools"}. A list of turns is also accepted."""
    name = "scripted"

    def __init__(self, script, model: str = "scripted-1"):
        self.script = script
        self.model = model
        self.sessions: list[_ScriptedSession] = []

    def start(self, system, history, tools):
        s = _ScriptedSession(self, system, history, tools)
        self.sessions.append(s)
        return s


class _ScriptedSession:
    def __init__(self, p: ScriptedProvider, system, history, tools):
        self.p, self.system, self.history = p, system, history
        self.tools = [t["name"] for t in tools]
        self.results: list[dict] = []
        self.notes: list[str] = []
        self.n = 0

    async def step(self, allow_tools: bool = True) -> ModelTurn:
        state = {"step": self.n, "question": self.history[-1][1] if self.history else "", "results": self.results,
                 "notes": self.notes, "tools": self.tools, "allow_tools": allow_tools, "system": self.system}
        if callable(self.p.script):
            out = self.p.script(state)
        else:
            out = self.p.script[min(self.n, len(self.p.script) - 1)]
        self.n += 1
        calls = [ToolCallReq(f"call_{self.n}_{i}", c["name"], c.get("args", {}))
                 for i, c in enumerate(out.get("calls") or [])] if allow_tools else []
        return ModelTurn(text=out.get("text", ""), calls=calls, input_tokens=out.get("input_tokens", 100),
                         output_tokens=out.get("output_tokens", 20), stop="tool_use" if calls else "end",
                         model=self.p.model)

    def add_tool_results(self, results):
        for _cid, content, _err in results:
            self.results.append(json.loads(content[content.index("{"):]))

    def add_user_note(self, text):
        self.notes.append(text)


# ── demo script (AI_PROVIDER=scripted, dev only: browser checks of /ai without a model key) ────────────────

def _first_number(data):
    if isinstance(data, bool) or data is None:
        return None
    if isinstance(data, (int, float)):
        return data
    items = data.values() if isinstance(data, dict) else data if isinstance(data, list) else []
    for v in items:
        x = _first_number(v)
        if x is not None:
            return x
    return None


def demo_script(state: dict) -> dict:
    """Keyword router: one read tool for the usual questions, then an answer quoting one number per result
    with its citation. Deterministic; never used in production."""
    import re as _re
    import unicodedata as _ud
    q = _ud.normalize("NFKD", state["question"]).encode("ascii", "ignore").decode().lower()
    ctx = {}
    m = _re.search(r'"line_id": "([0-9a-f-]{36})"', state.get("system", ""))
    if m:
        ctx["line_id"] = m.group(1)
    if state["step"] == 0 and state["allow_tools"]:
        if _re.match(r"\s*(abra|crie|abrir|criar|reconheca|mude|apague)\b", q):
            return {"text": "[[recusa:write_not_allowed]] Eu só consulto dados. Para abrir a OS use "
                            "[Nova OS](/maintenance/work-orders?new=1)."}
        if "oee" in q and ctx.get("line_id"):
            args = {"scope": "line", "id": ctx["line_id"], "period": "yesterday" if "ontem" in q else "today"}
            if "turno" in q:
                args["group_by"] = "shift"
            return {"calls": [{"name": "get_oee", "args": args}]}
        if "sem motivo" in q or "justific" in q:
            return {"calls": [{"name": "get_downtime", "args": {"period": "today", "unjustified": True}}]}
        if "minhas os" in q or "minhas ordens" in q:
            return {"calls": [{"name": "get_work_orders", "args": {"assignee_id": "me"}}]}
        if "parad" in q or "status" in q or "agora" in q:
            return {"calls": [{"name": "get_machine_status", "args": {}}]}
        return {"text": "[[recusa:out_of_scope]] Modo demonstração: pergunte sobre o que está parado agora, o OEE "
                        "desta linha hoje, paradas sem motivo ou as suas OS."}
    lines = ["Resposta (modo demonstração, dados reais das ferramentas):"]
    for r in state["results"]:
        if r.get("status") != "ok" or not r.get("citation"):
            lines.append(f"- a consulta não retornou dados ({r.get('message') or r.get('status')}).")
            continue
        x = _first_number(r.get("data"))
        if x is None:
            lines.append(f"- nenhum registro [{r['citation']}]")
        else:
            txt = f"{int(x):,}".replace(",", ".") if float(x).is_integer() else f"{x:.2f}".replace(".", ",")
            lines.append(f"- primeiro valor do resultado: {txt} [{r['citation']}]")
    return {"text": "\n".join(lines)}
