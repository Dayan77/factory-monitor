"""Driver `ueapd` (boxes ueaPowerDriver) contra um box falso em processo (aiohttp) que reproduz os formatos reais
da API do box (ueapowerdriver plc/app/api/auto.py + plc/app/auto/trace.py: /auto/trace/files, /auto/trace/pull com
tail por byte cortado na última linha completa, /auto/state)."""

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from email.utils import formatdate
from pathlib import Path

import pytest
from aiohttp import web

from edge.src import gateway as gw_mod
from edge.src import provision as prov
from edge.src.clock import DeviceClock
from edge.src.counting import EdgeCounterStore
from edge.src.plant_map import PlantMapError, parse_plant_map, parse_ueapd_selector
from edge.src.protocols.ueapd import UeapdDriver, pass_key, run_state_of
from edge.tests.conftest import free_port
from edge.tests.test_provision import api_server  # noqa: F401  (fixture)

EDGE = Path(__file__).resolve().parents[1]
EXAMPLE = EDGE / "config" / "ueapd.example.json"
DAY = 86400.0


# ── box falso ─────────────────────────────────────────────────────────────
class FakeBox:
    """Serviço :8003 do box: arquivos JSONL diários num diretório + estado vivo mutável."""

    def __init__(self, root: Path, line: str = "hipot", max_bytes: int = 1 << 20) -> None:
        self.dir = root
        self.dir.mkdir(parents=True, exist_ok=True)
        self.line = line
        self.max_bytes = max_bytes
        self.down = False
        self.clock_offset_s = 0.0          # relógio do box (cabeçalho Date)
        self.pass_seq = 0
        self.state = {"line": line, "armed": True, "active": True, "reconcile_pending": False,
                      "gate": {"armed": True, "mode_auto": True, "safety_ok": True, "emerg_ok": True,
                               "reset_ok": True, "ok": True},
                      "shift": {"ok": 0, "ng": 0}, "cadencia": {"ph": 200, "janela": 10}, "faults": [],
                      "stages": []}
        self.pulls = 0
        self.runner = None
        self.port = None

    # escrita igual a TraceLog.append / TraceLog.event (dia LOCAL do box pelo instante do registro)
    def _write(self, prefix: str, ts: float, rec: dict) -> Path:
        path = self.dir / time.strftime(f"{prefix}-%Y%m%d.jsonl", time.localtime(ts))
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return path

    def add_pass(self, ok: bool = True, finished_at: float | None = None, events=None, serial=None,
                 pass_id=None) -> dict:
        fin = time.time() if finished_at is None else finished_at
        self.pass_seq += 1
        rec = {"line": self.line, "pass_id": pass_id or self.pass_seq, "serial": serial or f"AUTO-{uuid.uuid4().hex[:8]}",
               "label": "", "started_at": fin - 60.0, "finished_at": fin,
               "events": events if events is not None else [
                   {"stage": "laser_1", "t": fin - 50, "ok": True, "posto": 0},
                   {"stage": "hipot_1", "t": fin - 30, "ok": ok, "posto": 0},
                   {"stage": "hipot_2", "t": fin - 10, "ok": True if ok else None, "posto": 0,
                    **({} if ok else {"skip": "pulado (NG anterior)"})}],
               "verdict": {"ok": ok, "reasons": [] if ok else ["NG em Hipot 1"]}}
        self._write("passes", fin, rec)
        return rec

    def add_manut(self, tipo: str, local: str, ts: float | None = None, componente: str = "clp",
                  dur_s: float | None = None) -> dict:
        ts = time.time() if ts is None else ts
        rec = {"ts": ts, "line": self.line, "componente": componente, "tipo": tipo, "detalhe": f"{local} ATIVA",
               "local": local}
        if dur_s is not None:
            rec["dur_s"] = dur_s
        self._write("manut", ts, rec)
        return rec

    # API (formatos de TraceLog.files / read_from e AutoOrchestrator.state)
    def files(self) -> list[dict]:
        out = []
        for pat in ("passes-*.jsonl", "manut-*.jsonl"):
            for p in sorted(self.dir.glob(pat)):
                d = p.stem.split("-")[1]
                out.append({"file": p.name, "date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "size": p.stat().st_size})
        return out

    def read_from(self, name: str, offset: int) -> dict:
        path = self.dir / os.path.basename(name)
        if not path.exists():
            return {"data": "", "next_offset": offset, "eof": True}
        with open(path, "rb") as f:
            f.seek(max(0, int(offset)))
            chunk = f.read(self.max_bytes)
            more = bool(f.read(1))
        if more:
            nl = chunk.rfind(b"\n")
            chunk = chunk[:nl + 1] if nl >= 0 else b""
        return {"data": chunk.decode("utf-8", errors="replace"), "next_offset": int(offset) + len(chunk),
                "eof": not more}

    @web.middleware
    async def _mw(self, request, handler):
        if self.down:
            return web.json_response({"detail": "orquestrador automático indisponível"}, status=503)
        resp = await handler(request)
        resp.headers["Date"] = formatdate(time.time() + self.clock_offset_s, usegmt=True)
        return resp

    async def h_files(self, request):
        return web.json_response({"files": self.files()})

    async def h_pull(self, request):
        self.pulls += 1
        return web.json_response(self.read_from(request.query["file"], int(request.query.get("offset", 0))))

    async def h_state(self, request):
        return web.json_response(self.state)

    async def start(self, port: int | None = None) -> str:
        app = web.Application(middlewares=[self._mw])
        app.router.add_get("/auto/trace/files", self.h_files)
        app.router.add_get("/auto/trace/pull", self.h_pull)
        app.router.add_get("/auto/state", self.h_state)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.port = port or free_port()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        return self.url

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None


@pytest.fixture
async def box(tmp_path):
    b = FakeBox(tmp_path / "trace")
    await b.start()
    yield b
    await b.stop()


TAGS = [
    ("run", "run_state", "state:run"), ("fault", "fault_state", "state:fault"),
    ("good", "part_counter_good", "passes:good"), ("scrap", "part_counter_scrap", "passes:scrap"),
    ("total", "part_counter_total", "passes:total"), ("cad", "analog", "state:cadence"),
    ("c_laser1", "cycle_counter", "cycles:laser_1"), ("c_hipot1", "cycle_counter", "cycles:hipot_1"),
    ("c_hipot2", "cycle_counter", "cycles:hipot_2"), ("c_ats_p2", "cycle_counter", "cycles:ats2_1:P2"),
    ("leak", "analog", "measure:hipot_1:hipot1.fuga_ma"),
]


def make_doc(url: str, **src) -> dict:
    return {
        "version": 1, "gateway_id": "gw-test", "tenant_id": "11111111-1111-1111-1111-111111111111",
        "sources": [{"id": "m1", "protocol": "ueapd", "base_url": url, "poll_interval_s": 0.2, "timeout_s": 2,
                     **src}],
        "tags": [{"key": f"ledd.m1.{k}", "tag_id": str(uuid.uuid5(uuid.NAMESPACE_URL, k)), "source": "m1",
                  "semantic_role": role, "address": addr} for k, role, addr in TAGS],
    }


class Collector:
    def __init__(self, pm) -> None:
        self.by_id = {t.tag_id: t.key.rsplit(".", 1)[1] for t in pm.tags}
        self.got: list[tuple[str, float, object, bool]] = []

    async def __call__(self, tag_id, value, ts=None, initial=False, refresh=False):
        self.got.append((self.by_id[tag_id], value, ts, initial))

    def values(self, name: str) -> list[float]:
        return [v for n, v, _, _ in self.got if n == name]

    def events(self, name: str) -> list[tuple[float, object]]:
        """Leituras com instante do box (bordas / passes), sem as re-ofertas do acumulado."""
        return [(v, ts) for n, v, ts, initial in self.got if n == name and initial]

    def clear(self) -> None:
        self.got.clear()


_DRIVERS: list[UeapdDriver] = []


def driver_for(url: str, store: EdgeCounterStore, clock=None, **src):
    pm = parse_plant_map(make_doc(url, **src), env={})
    col = Collector(pm)
    drv = UeapdDriver(pm.sources["m1"], pm.tags_for("m1"), store, on_reading=col, clock=clock)
    _DRIVERS.append(drv)
    return drv, col


@pytest.fixture(autouse=True)
async def _close_drivers():
    yield
    while _DRIVERS:
        await _DRIVERS.pop().stop()


@pytest.fixture
def store(tmp_path):
    s = EdgeCounterStore(str(tmp_path / "cnt.db"))
    yield s
    s.close()


# ── seletores e mapa ──────────────────────────────────────────────────────
def test_selector_parsing_and_role_validation(box):
    assert parse_ueapd_selector("cycles:ats2_1:P2") == ("cycles", "ats2_1", 1)
    assert parse_ueapd_selector("cycles:hipot_1") == ("cycles", "hipot_1", None)
    assert parse_ueapd_selector("measure:hipot_2:hipot2.v") == ("measure", "hipot_2", ["hipot2", "v"])
    for bad in ("cycles:", "cycles:a b", "cycles:x:3", "measure:x", "foo", "", None, 3):
        with pytest.raises(ValueError):
            parse_ueapd_selector(bad)
    doc = make_doc(box.url)
    doc["tags"][0]["semantic_role"] = "analog"                       # state:run como analog: recusado
    with pytest.raises(PlantMapError, match="exige semantic_role"):
        parse_plant_map(doc, env={})
    for bad_src, msg in (({"base_url": "192.168.1.15:8003"}, "base_url"), ({"backfill": "ontem"}, "backfill"),
                         ({"fault_codes": {"ERRO_CLAMP": "401"}}, "fault_codes"),
                         ({"poll_interval_s": 0}, "poll_interval_s")):
        d = make_doc(box.url)
        d["sources"][0].update(bad_src)
        with pytest.raises(PlantMapError, match=msg):
            parse_plant_map(d, env={})
    pm = parse_plant_map(make_doc(box.url, timestamps="source", max_clock_skew_s=2), env={})
    assert pm.sources["m1"].timestamps == "source" and pm.sources["m1"].backfill == "none"


def test_example_map_loads_and_covers_the_line():
    doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    pm = parse_plant_map(doc, env={})
    assert set(pm.sources) == {"ueapd-m1", "ueapd-m2"} and pm.tags == []       # tag_id null até provisionar
    assert pm.sources["ueapd-m1"].base_url == "http://192.168.1.15:8003"
    assert pm.sources["ueapd-m2"].base_url == "http://192.168.1.16:8003"
    assert doc["assets"]["plant"]["name"] == "Inventus Power"
    line = doc["assets"]["lines"][0]
    assert line["name"] == "Linha LED Driver" and [m["name"] for m in line["machines"]] == ["M1 Hipot", "M2 ATS2"]
    keys = {t["key"] for t in doc["tags"]}
    for m in line["machines"]:
        assert all(p["counter_tag"] in keys for p in m["positions"])
    addrs = {t["address"] for t in doc["tags"]}
    assert {"cycles:laser_1", "cycles:laser_2", "cycles:hipot_1", "cycles:hipot_2", "cycles:leitura",
            "cycles:ats2_1", "cycles:ats2_2", "cycles:funcional"} <= addrs
    env = {"UEAPD_M1_URL": "http://127.0.0.1:18003", "UEAPD_TIMESTAMPS": "source"}
    assert parse_plant_map(doc, env=env).sources["ueapd-m1"].timestamps == "source"


async def test_provision_creates_line_with_http_tags_and_counter_positions(api_server):  # noqa: F811
    fake, url = await api_server()
    doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    out, stats = await prov.provision(url, "admin@x", "secret", doc, log=lambda *a, **k: None)
    assert [p["name"] for p in fake.db["plants"].values()] == ["Inventus Power"]
    assert sorted(m["name"] for m in fake.db["machines"].values()) == ["M1 Hipot", "M2 ATS2"]
    tags = {(fake.db["machines"][t["machine_id"]]["name"], t["name"]): t for t in fake.db["tags"].values()}
    assert len(tags) == len(doc["tags"]) and all(t["protocol"] == "http" for t in tags.values())
    assert tags[("M2 ATS2", "Peças boas (veredito OK)")]["address"] == "passes:good"
    hip = tags[("M1 Hipot", "Ciclos Hipot 1")]
    assert hip["address"] == "cycles:hipot_1" and hip["semantic_role"] == "cycle_counter"
    pos = fake.db["positions"][hip["position_id"]]
    assert pos["name"] == "Hipot 1" and pos["counter_tag_id"] == hip["id"]
    pm = parse_plant_map(out, env={})
    assert not pm.skipped and len(pm.tags) == len(doc["tags"])
    out2, _ = await prov.provision(url, "admin@x", "secret", doc, log=lambda *a, **k: None)
    assert [t["tag_id"] for t in out2["tags"]] == [t["tag_id"] for t in out["tags"]]    # idempotente


# ── contagem ──────────────────────────────────────────────────────────────
async def test_counts_good_scrap_and_station_cycles(box, store):
    drv, col = driver_for(box.url, store, backfill="all")
    now = time.time()
    box.add_pass(ok=True, finished_at=now - 30)
    box.add_pass(ok=False, finished_at=now - 20)     # NG no hipot 1 → hipot 2 pulado
    box.add_pass(ok=True, finished_at=now - 10, events=[
        {"stage": "laser_1", "t": now - 18, "ok": True, "auto": True},          # entrada sintética: não é uso
        {"stage": "laser_1", "t": now - 17, "ok": True, "posto": 0},
        {"stage": "hipot_1", "t": now - 15, "ok": False, "retest": 1, "posto": 0},  # reteste: o testador rodou
        {"stage": "hipot_1", "t": now - 14, "ok": True, "posto": 0, "detail": {"hipot1": {"fuga_ma": 0.42}}},
        {"stage": "hipot_2", "t": now - 12, "ok": None, "posto": 0, "detail": {"erro": "timeout ponte"}},
        {"stage": "ats2_1", "t": now - 11, "ok": True, "posto": 1},
        {"stage": "ats2_1", "t": now - 11, "ok": True, "posto": 0},
    ])
    await drv.poll_once()
    assert drv.totals() == {"ledd.m1.good": 2, "ledd.m1.scrap": 1, "ledd.m1.total": 3, "ledd.m1.c_laser1": 3,
                            "ledd.m1.c_hipot1": 4, "ledd.m1.c_hipot2": 1, "ledd.m1.c_ats_p2": 1}
    # cada passe vira uma leitura no instante do box (finished_at); acumulado, nunca delta
    assert [v for v, _ in col.events("good")] == [1.0, 2.0]
    assert col.events("scrap")[0][1] == datetime.fromtimestamp(now - 20, tz=timezone.utc)
    assert [v for v, _ in col.events("leak")] == [0.42]
    assert col.events("leak")[0][1] == datetime.fromtimestamp(now - 14, tz=timezone.utc)
    assert col.values("cad")[-1] == 200 and col.values("run")[-1] == 1.0 and col.values("fault")[-1] == 0.0
    # nada novo: só re-ofertas do acumulado (o deadband do gateway decide), nenhum incremento
    col.clear()
    await drv.poll_once()
    assert col.events("good") == [] and col.values("good") == [2.0]
    assert store.get("ledd.m1.good") == 2


async def test_dedupe_across_restart_lost_offsets_and_recreated_file(box, store, tmp_path):
    drv, _ = driver_for(box.url, store, backfill="all")
    for i in range(5):
        box.add_pass(ok=i != 2)
    await drv.poll_once()
    await drv.stop()
    assert store.get("ledd.m1.good") == 4 and store.get("ledd.m1.scrap") == 1
    # reinício do gateway: mesmo SQLite, retoma do offset gravado
    drv2, col2 = driver_for(box.url, store, backfill="all")
    box.add_pass(ok=True)
    await drv2.poll_once()
    assert store.get("ledd.m1.good") == 5 and [v for v, _ in col2.events("good")] == [5.0]
    # offsets perdidos (ex.: estado copiado sem a tabela kv): relê tudo, o dedupe por passe segura
    store._db.execute("DELETE FROM edge_kv WHERE key LIKE 'ueapd:m1:off:%'")
    drv3, col3 = driver_for(box.url, store, backfill="all")
    await drv3.poll_once()
    assert store.get("ledd.m1.good") == 5 and col3.events("good") == [] and drv3.stats["duplicates"] == 6
    # box recriou o arquivo do dia (menor que o offset) com os mesmos passes + 1 novo: só o novo conta
    f = next(box.dir.glob("passes-*.jsonl"))
    lines = f.read_text(encoding="utf-8").splitlines(keepends=True)
    f.write_text("".join(lines[:2]), encoding="utf-8")
    box.add_pass(ok=False)
    await drv3.poll_once()
    assert drv3.stats["truncated_files"] == 1
    assert store.get("ledd.m1.good") == 5 and store.get("ledd.m1.scrap") == 2


async def test_box_restart_reuses_pass_id_but_is_counted(box, store):
    """O pass_id do box recomeça a cada reinício do serviço: (line, pass_id) sozinho não identifica o passe."""
    drv, _ = driver_for(box.url, store, backfill="all")
    a = box.add_pass(ok=True, pass_id=1, serial="S-1", finished_at=time.time() - 5)
    b = box.add_pass(ok=True, pass_id=1, serial="S-1", finished_at=time.time())
    assert pass_key(a) != pass_key(b)
    await drv.poll_once()
    assert store.get("ledd.m1.good") == 2


async def test_backfill_policies_on_first_contact(tmp_path):
    box = FakeBox(tmp_path / "t")
    await box.start()
    try:
        now = time.time()
        box.add_pass(finished_at=now - 2 * DAY)                 # dia anterior
        box.add_pass(finished_at=now - 60)
        box.add_pass(finished_at=now - 30)                      # hoje (2 passes)
        results = {}
        for mode in ("none", "latest_day", "all"):
            s = EdgeCounterStore(str(tmp_path / f"{mode}.db"))
            drv, _ = driver_for(box.url, s, backfill=mode)
            await drv.poll_once()
            results[mode] = s.get("ledd.m1.good")
            box.add_pass(finished_at=time.time())               # depois do 1º contato: sempre conta
            await drv.poll_once()
            results[mode + "+1"] = s.get("ledd.m1.good")
            await drv.stop()
            s.close()
        # cada modo deixa +1 passe de hoje no box para o seguinte
        assert results["none"] == 0 and results["none+1"] == 1             # só o que veio depois
        assert results["latest_day"] == 3 and results["latest_day+1"] == 4  # o dia mais recente inteiro
        assert results["all"] == 5 and results["all+1"] == 6                # tudo (dentro de DEDUPE_DAYS)
    finally:
        await box.stop()


async def test_day_rollover_and_late_append_to_previous_day(box, store):
    drv, col = driver_for(box.url, store, backfill="all")
    now = time.time()
    box.add_pass(finished_at=now - DAY)
    await drv.poll_once()
    box.add_pass(finished_at=now)                     # arquivo novo (dia seguinte) aparece depois do 1º contato
    box.add_pass(finished_at=now - DAY + 1)           # passe fechado no fim do dia anterior gravado no arquivo velho
    await drv.poll_once()
    assert len(list(box.dir.glob("passes-*.jsonl"))) == 2
    assert store.get("ledd.m1.good") == 3 and len(col.events("good")) == 3


async def test_partial_line_at_eof_and_chunked_pull(box, store):
    box.max_bytes = 700                                # cada pull cabe ~uma linha: vários pulls por arquivo
    drv, col = driver_for(box.url, store, backfill="all")
    for _ in range(4):
        box.add_pass()
    rec = {"line": "hipot", "pass_id": 99, "serial": "ÇÃO-ü", "started_at": time.time() - 9,
           "finished_at": time.time(), "events": [], "verdict": {"ok": True, "reasons": []}}
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    f = next(box.dir.glob("passes-*.jsonl"))
    raw = line.encode("utf-8")
    cut = raw.index("Ç".encode("utf-8")) + 1          # meio de um caractere multibyte: box no meio da escrita
    with open(f, "ab") as fh:
        fh.write(raw[:cut])
    await drv.poll_once()
    assert box.pulls >= 3 and store.get("ledd.m1.good") == 4       # vários pulls; a linha cortada fica
    with open(f, "ab") as fh:
        fh.write(raw[cut:])
    await drv.poll_once()
    assert store.get("ledd.m1.good") == 5 and drv.stats["bad_lines"] == 0
    assert int(store.get_kv(f"ueapd:m1:off:{f.name}")) == f.stat().st_size


# ── falhas e estado ───────────────────────────────────────────────────────
async def test_fault_code_mapping_from_plc_fault_edges(box, store):
    drv, col = driver_for(box.url, store, fault_codes={"SAFETY_CONVEYOR": 199})
    await drv.poll_once()                              # 1º contato (backfill none): manut começa do fim
    t0 = time.time()
    box.add_manut("falha_clp", "ERRO_CLAMP", ts=t0 - 8, componente="clamp")
    box.add_manut("falha_clp", "ERRO_STOP", ts=t0 - 7, componente="stopper")   # 2ª falha: mantém a 1ª
    box.add_manut("falha_clp_fim", "ERRO_CLAMP", ts=t0 - 6, componente="clamp", dur_s=2.0)
    box.add_manut("falha_clp_fim", "ERRO_STOP", ts=t0 - 5, componente="stopper", dur_s=2.0)
    box.add_manut("falha_clp", "GRAVADORA_ERRO", ts=t0 - 4)                     # laser (componente "clp")
    box.add_manut("falha_clp_fim", "GRAVADORA_ERRO", ts=t0 - 3, dur_s=1.0)
    box.add_manut("falha_clp", "SAFETY_CONVEYOR", ts=t0 - 2, componente="esteira")   # código do mapa
    box.add_manut("releitura_auto", "leitura", ts=t0 - 1.5, componente="leitor")    # não é falha do CLP
    box.state["faults"] = [{"tag": "SAFETY_CONVEYOR", "label": "Safety conveyor"}]
    await drv.poll_once()
    ev = col.events("fault")
    assert [v for v, _ in ev] == [401.0, 102.0, 0.0, 501.0, 0.0, 199.0]
    assert ev[0][1] == datetime.fromtimestamp(t0 - 8, tz=timezone.utc)      # instante da borda no box
    assert col.values("run")[-1] == 0.0                                     # falha ativa → não está rodando
    # tag desconhecida no estado vivo (sem borda no manut: box sem trace) → código padrão 3xx, agora
    col.clear()
    box.state["faults"] = [{"tag": "SAFETY_CONVEYOR", "label": "x"}, {"tag": "ERRO_NOVO", "label": "?"}]
    await drv.poll_once()
    assert col.values("fault")[-1] == 199.0                                 # a mais antiga continua
    box.state["faults"] = [{"tag": "ERRO_NOVO", "label": "?"}]
    await drv.poll_once()
    assert col.values("fault")[-1] == 301.0 and col.events("fault") == []
    # a borda de descida chega depois (backlog) com instante já passado: não reescreve o passado
    box.state["faults"] = []
    await drv.poll_once()
    assert col.values("fault")[-1] == 0.0 and col.values("run")[-1] == 1.0
    box.add_manut("falha_clp_fim", "SAFETY_CONVEYOR", ts=t0 - 1, componente="esteira", dur_s=1.0)
    col.clear()
    await drv.poll_once()
    assert col.events("fault") == [] and col.values("fault") == [0.0]
    # o estado das falhas sobrevive ao reinício do gateway
    box.add_manut("falha_clp", "ERRO_TRANSFERENCIA", ts=time.time(), componente="transferencia")
    box.state["faults"] = [{"tag": "ERRO_TRANSFERENCIA", "label": "Erro de transferência"}]
    await drv.poll_once()
    drv2, col2 = driver_for(box.url, store, fault_codes={"SAFETY_CONVEYOR": 199})
    await drv2.poll_once()
    assert col2.values("fault") == [101.0] and drv2._fault_value == 101


@pytest.mark.parametrize("patch, expected", [
    ({}, 1),
    ({"armed": False}, 0),
    ({"gate": {"armed": True, "mode_auto": False, "safety_ok": True, "emerg_ok": True, "ok": False}}, 0),
    ({"gate": {"ok": False, "safety_ok": False}}, 0),
    ({"reconcile_pending": True}, 0),
    ({"faults": [{"tag": "ERRO_STOP", "label": "Erro de stop"}]}, 0),
    ({"gate": {}}, 0),                                 # gate ainda não lido no boot do box
])
async def test_run_state_rule(box, store, patch, expected):
    drv, col = driver_for(box.url, store)
    box.state.update(patch)
    await drv.poll_once()
    assert col.values("run") == [float(expected)]
    assert run_state_of(box.state, bool(box.state["faults"])) == expected


# ── conexão e relógio ─────────────────────────────────────────────────────
async def test_box_unreachable_reconnects_without_losing_passes(tmp_path, store):
    box = FakeBox(tmp_path / "t")
    port = free_port()
    await box.start(port)
    drv, col = driver_for(box.url, store, backfill="all")
    drv.max_backoff_s = 0.4
    task = asyncio.create_task(drv.run())
    try:
        box.add_pass()
        for _ in range(100):
            if store.get("ledd.m1.good") == 1 and drv.connected:
                break
            await asyncio.sleep(0.05)
        assert drv.connected and store.get("ledd.m1.good") == 1
        await box.stop()                                 # rede/box fora; o box continua produzindo
        for _ in range(3):
            box.add_pass()
        for _ in range(100):
            if not drv.connected:
                break
            await asyncio.sleep(0.05)
        assert not drv.connected and drv.stats["errors"] >= 1
        n_before = len(col.got)
        await asyncio.sleep(0.5)
        assert len(col.got) == n_before                  # box fora: nada é republicado como se fosse novo
        box.down = True                                  # volta respondendo 503 (orquestrador subindo)
        await box.start(port)
        await asyncio.sleep(0.5)
        assert not drv.connected and store.get("ledd.m1.good") == 1
        box.down = False
        for _ in range(100):                             # contagem e o flag de conexão chegam em polls distintos
            if store.get("ledd.m1.good") == 4 and drv.connected:
                break
            await asyncio.sleep(0.05)
        assert store.get("ledd.m1.good") == 4 and drv.connected
        assert [v for v, _ in col.events("good")] == [1.0, 2.0, 3.0, 4.0]
    finally:
        await drv.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await box.stop()


@pytest.fixture
async def gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(gw_mod.settings, "buffer_db_path", str(tmp_path / "buf.db"))
    monkeypatch.setattr(gw_mod.settings, "counter_db_path", str(tmp_path / "gwcnt.db"))
    monkeypatch.setattr(gw_mod.settings, "tenant_id", "")
    monkeypatch.setattr(gw_mod.settings, "gateway_id", "")
    monkeypatch.setattr(gw_mod.settings, "encryption_key", "")
    g = gw_mod.EdgeGateway()
    await g.buffer.initialize()

    class Client:
        def __init__(self):
            self.sent = []

        async def publish(self, topic, payload, qos=0):
            self.sent.append((topic, json.loads(payload)))

    g.publisher._client, g.publisher.is_connected = Client(), True
    yield g
    for d in g.drivers:
        await d.stop()
    await g.buffer.close()
    if g.counter_store:
        g.counter_store.close()


def _published(g, pm, name):
    tid = next(t.tag_id for t in pm.tags if t.key.endswith("." + name))
    return [(p["value"], datetime.fromisoformat(p["ts"].replace("Z", "+00:00")))
            for topic, p in g.publisher._client.sent if topic.endswith("/" + tid)]


@pytest.mark.parametrize("policy", ["source", "gateway"])
async def test_gateway_path_and_clock_policy(gateway, box, policy):
    pm = parse_plant_map(make_doc(box.url, timestamps=policy, max_clock_skew_s=2, backfill="all"), env={})
    gateway.load_plant_map(pm)
    drv = next(d for d in gateway.drivers if isinstance(d, UeapdDriver))
    assert drv.clock is gateway._clocks["m1"]
    fin = time.time() - 120                              # passe fechado há 2 min (backlog)
    box.add_pass(finished_at=fin)
    before = datetime.now(timezone.utc)
    await drv.poll_once()
    good = _published(gateway, pm, "good")
    assert good[0][0] == 1 and {v for v, _ in good} == {1}     # (+ re-envio de silêncio no relógio da leitura)
    if policy == "source":
        assert abs((good[0][1] - datetime.fromtimestamp(fin, tz=timezone.utc)).total_seconds()) < 0.002
    else:
        assert good[0][1] >= before.replace(microsecond=before.microsecond - before.microsecond % 1000)
    assert _published(gateway, pm, "run")[-1][0] == 1
    st = gateway.heartbeat_payload()["clocks"]["m1"]
    assert st["configured"] == policy and st["policy"] == policy and abs(st["skew_s"]) < 1.6
    assert gateway.source_status() == {"m1": False}        # `connected` só no laço run()


async def test_source_clock_ahead_is_demoted_to_gateway(gateway, box):
    pm = parse_plant_map(make_doc(box.url, timestamps="source", max_clock_skew_s=2, backfill="all"), env={})
    gateway.load_plant_map(pm)
    drv = next(d for d in gateway.drivers if isinstance(d, UeapdDriver))
    box.clock_offset_s = 30.0                            # relógio do box 30 s adiantado
    fin = time.time() - 120
    box.add_pass(finished_at=fin)
    await drv.poll_once()
    assert gateway._clocks["m1"].effective_policy == "gateway"
    good = _published(gateway, pm, "good")
    assert abs((good[0][1] - datetime.now(timezone.utc)).total_seconds()) < 5   # hora do gateway, não a do box


def test_driver_clock_sample_uses_date_header():
    clock = DeviceClock("m1", "source", 2.0)
    pm = parse_plant_map(make_doc("http://127.0.0.1:1"), env={})
    drv = UeapdDriver(pm.sources["m1"], pm.tags_for("m1"), store=None, on_reading=None, clock=clock)
    recv = datetime.now(timezone.utc)
    drv._clock_sample(formatdate(recv.timestamp() - 10, usegmt=True), recv)
    assert -10.6 < clock.offset_s < -9.4
