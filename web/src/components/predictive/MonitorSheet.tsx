/**
 * Monitor configuration (predictive.md P-1…P-3, P-6): signal window, failure limit, on/off, rules (threshold,
 * % of baseline, rate) and the manual rebaseline ("novo normal", reason required). Changing the window, tag or
 * direction restarts the baseline (server-side, P-2) — the form says so before saving.
 * Read-only for roles without `predictive.configure`.
 */
import { useEffect, useState } from "react";
import clsx from "clsx";
import Sheet from "@/components/ui/Sheet";
import Button from "@/components/ui/Button";
import Badge from "@/components/ui/Badge";
import QueryView from "@/components/ui/QueryView";
import { Field, inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { ConditionPanel, SeverityPill } from "./parts";
import { useDeleteRule, useMonitor, useRebaseline, useSaveMonitor, useSaveRule } from "@/hooks/usePredictive";
import { usePlantTree } from "@/hooks/useAssets";
import { usePositionsIndex } from "@/hooks/useMaintenance";
import { useAuthStore } from "@/store/auth";
import type { Feature, Monitor, PredRule, PredSeverity, RuleType } from "@/services/predictiveTypes";
import { can } from "@/utils/maintenance";
import {
  FEATURE_LABEL, FEATURE_UNIT, FEATURES, formatSignal, RULE_TYPE_LABEL, ruleText, SEVERITY_LABEL, windowText,
} from "@/utils/predictive";
import { formatDateTime } from "@/utils/format";
import { apiErrorMessage } from "@/utils/errors";

const SEVERITIES: PredSeverity[] = ["watch", "warning", "critical"];
const RULE_TYPES: RuleType[] = ["threshold", "pct_of_baseline", "rate_of_change"];

function parseNum(s: string): number | null {
  if (!s.trim()) return null;
  const n = Number(s.replace(",", "."));
  return Number.isFinite(n) ? n : NaN;
}

function ConfigForm({ m }: { m: Monitor }) {
  const save = useSaveMonitor();
  const isCycles = m.feature === "stroke_time";
  const [win, setWin] = useState(String(isCycles ? m.window.cycles ?? "" : m.window.hours ?? ""));
  const [limit, setLimit] = useState(m.failure_limit == null ? "" : String(m.failure_limit_relative ? Math.round(m.failure_limit * 1000) / 10 : m.failure_limit));
  const [relative, setRelative] = useState(m.failure_limit_relative);
  const [enabled, setEnabled] = useState(m.enabled);
  const [msg, setMsg] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const origWin = String(isCycles ? m.window.cycles ?? "" : m.window.hours ?? "");
  const resets = win !== origWin;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    const w = parseNum(win);
    const l = parseNum(limit);
    if (Number.isNaN(w) || Number.isNaN(l) || (w != null && w <= 0)) { setMsg({ tone: "danger", text: "Valores inválidos." }); return; }
    try {
      await save.mutateAsync({ id: m.id, body: {
        ...(resets ? (isCycles ? { window_cycles: w } : { window_hours: w }) : {}),
        failure_limit: l == null ? null : relative ? l / 100 : l,
        failure_limit_relative: relative,
        enabled,
        reason: resets ? "janela alterada na tela de monitores" : null,
      } });
      setMsg({ tone: "ok", text: resets ? "Salvo. A linha de base recomeça (com aquecimento)." : "Configuração salva." });
    } catch (err) { setMsg({ tone: "danger", text: apiErrorMessage(err, "Não foi possível salvar.") }); }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-3" data-testid="monitor-config">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={isCycles ? "Janela (ciclos por bloco)" : "Janela (horas)"} htmlFor="mon-window" hint={resets ? "Mudar a janela reinicia a linha de base." : `Atual: ${windowText(m.window)}`}>
          <input id="mon-window" inputMode="decimal" value={win} onChange={(e) => setWin(e.target.value)} className={inputClass} />
        </Field>
        <Field label={relative ? "Limite de falha (% da referência)" : `Limite de falha (${m.unit})`} htmlFor="mon-limit"
          hint={m.failure_limit_value != null ? `Hoje: ${formatSignal(m.failure_limit_value, m.unit)}` : "Sem limite: não há projeção de RUL."}>
          <input id="mon-limit" inputMode="decimal" value={limit} onChange={(e) => setLimit(e.target.value)} className={inputClass} />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-body text-fg">
        <input type="checkbox" checked={relative} onChange={(e) => setRelative(e.target.checked)} /> Limite relativo à referência (ex.: 120 % para tempo de curso)
      </label>
      <label className="flex items-center gap-2 text-body text-fg">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Monitoramento ligado
      </label>
      <div className="flex items-center gap-3">
        <Button type="submit" variant="primary" loading={save.isPending}>Salvar configuração</Button>
        {msg && <p role={msg.tone === "danger" ? "alert" : "status"} className={clsx("text-body", msg.tone === "danger" ? "text-danger" : "text-ok")}>{msg.text}</p>}
      </div>
    </form>
  );
}

function RuleRow({ r, m, editable }: { r: PredRule; m: Monitor; editable: boolean }) {
  const save = useSaveRule();
  const del = useDeleteRule();
  const [err, setErr] = useState("");
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 px-3 py-2" data-testid="rule-row">
      <div className="min-w-0">
        <p className="text-body font-medium text-fg"><span className="text-fg-subtle">{r.code}</span> · {RULE_TYPE_LABEL[r.type]} {ruleText(r, m.unit, m.baseline?.m0)}</p>
        <p className="text-caption text-fg-subtle">{SEVERITY_LABEL[r.severity]}{r.description ? ` · ${r.description}` : ""}{!r.enabled ? " · desligada" : ""}</p>
        {err && <p role="alert" className="text-caption text-danger">{err}</p>}
      </div>
      <div className="flex items-center gap-1">
        <SeverityPill severity={r.severity} />
        {editable && (
          <>
            <Button size="sm" variant="ghost" loading={save.isPending}
              onClick={() => save.mutateAsync({ id: r.id, body: { enabled: !r.enabled } }).catch((e) => setErr(apiErrorMessage(e, "Falhou.")))}>
              {r.enabled ? "Desligar" : "Ligar"}
            </Button>
            <Button size="sm" variant="danger-ghost" loading={del.isPending} aria-label={`Excluir regra ${r.code}`}
              onClick={() => { if (window.confirm(`Excluir a regra ${r.code}?`)) del.mutateAsync(r.id).catch((e) => setErr(apiErrorMessage(e, "Falhou."))); }}>
              Excluir
            </Button>
          </>
        )}
      </div>
    </li>
  );
}

function NewRuleForm({ m, onDone }: { m: Monitor; onDone: () => void }) {
  const save = useSaveRule();
  const [type, setType] = useState<RuleType>("pct_of_baseline");
  const [value, setValue] = useState("");
  const [sustained, setSustained] = useState("2");
  const [severity, setSeverity] = useState<PredSeverity>("warning");
  const [err, setErr] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    const v = parseNum(value);
    const s = parseNum(sustained);
    if (v == null || Number.isNaN(v) || s == null || Number.isNaN(s) || s < 1) { setErr("Informe valor e janelas seguidas (≥ 1)."); return; }
    try {
      await save.mutateAsync({ body: { monitor_id: m.id, type, value: type === "pct_of_baseline" ? v / 100 : v, sustained_for: Math.round(s), severity } });
      onDone();
    } catch (er) { setErr(apiErrorMessage(er, "Não foi possível criar a regra.")); }
  }
  const valueLabel = type === "threshold" ? `Valor (${m.unit})` : type === "pct_of_baseline" ? "% da referência (ex.: 110)" : `Inclinação (${m.unit} por ${m.feature === "stroke_time" ? "ciclo" : "hora de operação"})`;
  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-3 rounded-xl border border-line p-3" data-testid="new-rule">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Tipo" htmlFor="rule-type">
          <select id="rule-type" value={type} onChange={(e) => setType(e.target.value as RuleType)} className={inputClass}>
            {RULE_TYPES.map((t) => <option key={t} value={t}>{RULE_TYPE_LABEL[t]}</option>)}
          </select>
        </Field>
        <Field label={valueLabel} htmlFor="rule-value">
          <input id="rule-value" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} className={inputClass} />
        </Field>
        <Field label="Janelas válidas seguidas" htmlFor="rule-sus">
          <input id="rule-sus" inputMode="numeric" value={sustained} onChange={(e) => setSustained(e.target.value)} className={inputClass} />
        </Field>
        <Field label="Severidade" htmlFor="rule-sev">
          <select id="rule-sev" value={severity} onChange={(e) => setSeverity(e.target.value as PredSeverity)} className={inputClass}>
            {SEVERITIES.map((s) => <option key={s} value={s}>{SEVERITY_LABEL[s]}</option>)}
          </select>
        </Field>
      </div>
      {err && <p role="alert" className="text-body text-danger">{err}</p>}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" loading={save.isPending}>Adicionar regra</Button>
        <Button variant="ghost" onClick={onDone}>Cancelar</Button>
      </div>
    </form>
  );
}

function RebaselineForm({ m }: { m: Monitor }) {
  const rb = useRebaseline();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  if (!open) return (
    <div className="space-y-1">
      <Button onClick={() => { setOpen(true); setMsg(null); }}>Reiniciar linha de base…</Button>
      {msg && <p role="status" className={clsx("text-body", msg.tone === "ok" ? "text-ok" : "text-danger")}>{msg.text}</p>}
    </div>
  );
  return (
    <div className="space-y-2 rounded-xl border border-line p-3">
      <p className="text-body text-fg">"Novo normal" aceito (ex.: mudança de produto ou carga). Os detectores voltam a aprender, sem aquecimento. O motivo fica na trilha de auditoria.</p>
      <Field label="Motivo (obrigatório)" htmlFor="rb-reason">
        <input id="rb-reason" value={reason} onChange={(e) => setReason(e.target.value)} className={inputClass} />
      </Field>
      <div className="flex gap-2">
        <Button variant="primary" disabled={reason.trim().length < 3} loading={rb.isPending}
          onClick={() => rb.mutateAsync({ id: m.id, reason: reason.trim() })
            .then(() => { setOpen(false); setReason(""); setMsg({ tone: "ok", text: "Linha de base reiniciada." }); })
            .catch((e) => setMsg({ tone: "danger", text: apiErrorMessage(e, "Não foi possível reiniciar.") }))}>
          Reiniciar
        </Button>
        <Button variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button>
      </div>
      {msg?.tone === "danger" && <p role="alert" className="text-body text-danger">{msg.text}</p>}
    </div>
  );
}

function MonitorBody({ m }: { m: Monitor }) {
  const user = useAuthStore((s) => s.user);
  const editable = can(user, "predictive.configure");
  const [adding, setAdding] = useState(false);
  return (
    <div className="space-y-6">
      <ConditionPanel positionId={m.position_id} feature={m.feature} />
      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-overline uppercase text-fg-subtle">Regras de condição</h3>
          {editable && !adding && <Button size="sm" onClick={() => setAdding(true)}>Nova regra</Button>}
        </div>
        {(m.rules ?? []).length === 0 ? <p className="text-caption text-fg-subtle">Nenhuma regra: só os detectores estatísticos (Atenção) avaliam.</p> : (
          <ul className="divide-y divide-line rounded-xl border border-line">
            {(m.rules ?? []).map((r) => <RuleRow key={r.id} r={r} m={m} editable={editable} />)}
          </ul>
        )}
        {adding && <NewRuleForm m={m} onDone={() => setAdding(false)} />}
      </section>
      {editable && (
        <section className="space-y-3">
          <h3 className="text-overline uppercase text-fg-subtle">Configuração do sinal</h3>
          <ConfigForm key={`${m.id}-${m.enabled}-${m.failure_limit}`} m={m} />
          <RebaselineForm m={m} />
        </section>
      )}
      {(m.baselines ?? []).length > 0 && (
        <section className="space-y-2">
          <h3 className="text-overline uppercase text-fg-subtle">Linhas de base</h3>
          <ol className="space-y-1.5 text-caption">
            {[...(m.baselines ?? [])].reverse().map((b) => (
              <li key={b.id} className="text-fg-muted">
                <span className="font-medium text-fg">{b.state === "established" ? `${formatSignal(b.m0, null)} ± ${formatSignal(b.sigma, m.unit)}` : "aprendendo"}</span>
                {" · "}reinício {formatDateTime(b.reset_at)} ({b.reset_kind}{b.warmup ? ", com aquecimento" : ""}){b.reason ? ` — ${b.reason}` : ""}
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

export default function MonitorSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useMonitor(id);
  const m = q.data;
  return (
    <Sheet open={!!id} onClose={onClose} size="lg"
      title={m ? `${m.position_name ?? "Posição"} — ${FEATURE_LABEL[m.feature]}` : "Monitor de condição"}
      description={m ? `${m.machine_name ?? ""} · janela ${windowText(m.window)}` : undefined}>
      <QueryView query={q} errorTitle="Não foi possível carregar o monitor"
        loading={<LoadingRegion label="Carregando monitor…"><Skeleton className="h-64" /></LoadingRegion>}>
        {(d) => <MonitorBody m={d} />}
      </QueryView>
    </Sheet>
  );
}

/** "Ligar a preditiva" numa posição: sinal, janela e limite de falha; regras sugeridas pelo servidor (P-3). */
export function NewMonitorSheet({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (m: Monitor) => void }) {
  const tree = usePlantTree();
  const save = useSaveMonitor();
  const machines = (tree.data ?? []).flatMap((p) => p.lines.flatMap((l) => l.machines.map((mm) => ({ ...mm, line: l.name }))));
  const [machineId, setMachineId] = useState("");
  const [positionId, setPositionId] = useState("");
  const [feature, setFeature] = useState<Feature>("stroke_time");
  const [limit, setLimit] = useState("");
  const [err, setErr] = useState("");
  const positions = usePositionsIndex([machineId]);
  const posList = [...positions.values()].filter((p) => p.machine_id === machineId);
  useEffect(() => { if (open) { setErr(""); setLimit(""); } }, [open]);

  async function submit() {
    setErr("");
    const l = parseNum(limit);
    if (!positionId) { setErr("Escolha a posição."); return; }
    if (Number.isNaN(l)) { setErr("Limite inválido."); return; }
    try {
      const m = await save.mutateAsync({ body: {
        position_id: positionId, feature, default_rules: true,
        ...(l != null ? (feature === "stroke_time" ? { failure_limit: l / 100, failure_limit_relative: true } : { failure_limit: l, failure_limit_relative: false }) : {}),
      } });
      onCreated(m);
    } catch (e) { setErr(apiErrorMessage(e, "Não foi possível ligar o monitoramento.")); }
  }

  return (
    <Sheet open={open} onClose={onClose} title="Monitorar condição" description="Compara o componente com ele mesmo: a referência é aprendida logo após a instalação."
      footer={<div className="flex gap-2"><Button variant="primary" onClick={() => void submit()} loading={save.isPending}>Ligar monitoramento</Button><Button variant="ghost" onClick={onClose}>Cancelar</Button></div>}>
      <div className="space-y-4">
        <Field label="Máquina" htmlFor="nm-machine">
          <select id="nm-machine" value={machineId} onChange={(e) => { setMachineId(e.target.value); setPositionId(""); }} className={inputClass}>
            <option value="">Escolha…</option>
            {machines.map((mm) => <option key={mm.id} value={mm.id}>{mm.line} · {mm.name}</option>)}
          </select>
        </Field>
        <Field label="Posição" htmlFor="nm-position">
          <select id="nm-position" value={positionId} onChange={(e) => setPositionId(e.target.value)} className={inputClass} disabled={!machineId}>
            <option value="">{machineId && posList.length === 0 ? "Nenhuma posição nesta máquina" : "Escolha…"}</option>
            {posList.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </Field>
        <Field label="Sinal" htmlFor="nm-feature">
          <select id="nm-feature" value={feature} onChange={(e) => setFeature(e.target.value as Feature)} className={inputClass}>
            {FEATURES.map((f) => <option key={f} value={f}>{FEATURE_LABEL[f]} ({FEATURE_UNIT[f]})</option>)}
          </select>
        </Field>
        <Field label={feature === "stroke_time" ? "Limite de falha (% da referência)" : `Limite de falha (${FEATURE_UNIT[feature]})`} htmlFor="nm-limit"
          hint={feature === "stroke_time" ? "Vazio = 120 % da referência (padrão)." : feature === "vibration" ? "Sem padrão: informe o limite da zona C/D da ISO 20816 da máquina." : "Ex.: corrente nominal de placa; limite do mancal/isolação."}>
          <input id="nm-limit" inputMode="decimal" value={limit} onChange={(e) => setLimit(e.target.value)} className={inputClass} />
        </Field>
        <p className="text-caption text-fg-subtle">Janela padrão: {feature === "stroke_time" ? "blocos de 1.000 ciclos (mediana)" : "8 h de operação (média ponderada no tempo, só rodando)"}. Regras sugeridas são criadas e podem ser editadas.</p>
        {err && <p role="alert" className="text-body text-danger">{err}</p>}
        {!tree.isPending && machines.length === 0 && <Badge tone="warn">Nenhuma máquina cadastrada</Badge>}
      </div>
    </Sheet>
  );
}
