import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useRealtimeConnection } from "@/hooks/useWebSocket";
import { usePlantMap } from "@/hooks/usePlantMap";
import { useNow } from "@/hooks/useNow";
import { useDowntime, useMachineProducts, useOpenRun, useReasonCodes, useStartRun } from "@/hooks/useEfficiency";
import { useAuthStore } from "@/store/auth";
import { api } from "@/services/api";
import { cancel, enqueue, topReasons, type FlushReport } from "@/services/operatorQueue";
import { registerServiceWorker } from "@/services/pwa";
import type { DowntimeEvent, ReasonCode, TreeLine } from "@/services/types";
import type { MachineVisual } from "@/services/sprint6Types";
import StateIcon from "@/components/plantmap/StateIcon";
import CallMaintenanceButton from "@/components/maintenance/CallMaintenanceButton";
import { STATE_META, bySeverity, markerText } from "@/utils/visualState";
import { formatTimer, reasonLabel } from "@/utils/efficiency";
import { formatInt, formatPct } from "@/utils/format";
import { apiErrorMessage } from "@/utils/errors";
import { clockTime } from "@/utils/plantMap";
import { isDemoUser } from "@/utils/demo";
import { SUSPECT_TITLE, oeeText } from "@/utils/mapKpis";
import { OFFLINE_BANNER_MS, readLine, useConnectivity, useIdleLogout, useOperatorQueue, usePendingOwners, useTabletLine } from "./useOperator";
import { useSeedTabletSnapshot, useTabletSnapshot } from "./tabletCache";

const UNDO_MS = 5_000;

/** Big touch target (≥ 64 px for main actions, gloves). */
function BigButton({ children, onClick, to, tone = "neutral", className, testId, disabled }: {
  children: ReactNode; onClick?: () => void; to?: string; tone?: "neutral" | "accent" | "warn" | "danger"; className?: string; testId?: string; disabled?: boolean;
}) {
  const cls = clsx(
    "flex min-h-[4.5rem] w-full items-center justify-center gap-3 rounded-2xl border-2 px-5 text-center text-[1.25rem] font-semibold transition-colors",
    "focus-visible:outline focus-visible:outline-4 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50",
    tone === "accent" && "border-[#14532d] bg-[#15803d] text-white active:bg-[#166534]",
    tone === "warn" && "border-[#b45309] bg-[#f59e0b] text-[#111827] active:bg-[#d97706]",
    tone === "danger" && "border-[#7f1d1d] bg-[#b91c1c] text-white active:bg-[#991b1b]",
    tone === "neutral" && "border-line-strong bg-surface text-fg active:bg-surface-2",
    className,
  );
  if (to) return <Link to={to} className={cls} data-testid={testId}>{children}</Link>;
  return <button type="button" onClick={onClick} className={cls} data-testid={testId} disabled={disabled}>{children}</button>;
}

/** Operator tablet PWA (/op, docs/sprint6.md §3): one line, big current state, ≤ 3 taps for any action, offline queue. */
export default function OperatorApp() {
  const user = useAuthStore((s) => s.user);
  if (isDemoUser(user)) return <Navigate to="/dashboard" replace />;
  return <OperatorShell />;
}

function OperatorShell() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  useRealtimeConnection(user?.tenant_id);
  useEffect(() => { void registerServiceWorker(); }, []);
  // Offline after a (re)open: what the justify screens need comes from the device snapshot (acceptance 5).
  useSeedTabletSnapshot(readLine());
  const { tree, options, current, choose } = useTabletLine();
  useTabletSnapshot(current?.line.id ?? null);
  const conn = useConnectivity();
  const now = useNow(1000);
  const [notice, setNotice] = useState<{ tone: "ok" | "warn" | "danger"; text: string } | null>(null);
  const queue = useOperatorQueue((r: FlushReport) => {
    if (r.conflicts.length) {
      const c = r.conflicts[0];
      setNotice({ tone: "warn", text: `${c.message}${c.current?.reason_code ? ` Ficou: ${c.current.reason_code}.` : ""}` });
    } else if (r.rejected.length) {
      setNotice({ tone: "danger", text: `Não foi aceito: ${r.rejected[0].message}` });
    } else if (r.sent.length) {
      conn.markSuccess();
    }
    if (r.remaining > 0 && !r.retryAt) conn.markFailure();
  });
  const [confirmLogout, setConfirmLogout] = useState(false);
  const signOut = () => { logout(); navigate("/op/login", { replace: true }); };
  // Never end the session while this operator still has actions waiting: they replay only with his session.
  useIdleLogout(signOut, undefined, queue.items.length > 0);

  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 6000);
    return () => clearTimeout(t);
  }, [notice]);

  const offlineFor = conn.since ? now - conn.since : 0;
  const offline = !conn.online || offlineFor > OFFLINE_BANNER_MS;

  return (
    <div className="flex min-h-[100dvh] flex-col bg-canvas text-[18px] leading-relaxed text-fg" data-testid="operator-app">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-surface px-4 py-3 sm:px-6">
        <div className="min-w-0">
          <p className="text-caption uppercase tracking-wide text-fg-subtle">Tablet do operador</p>
          <h1 className="truncate text-[1.5rem] font-bold">{current?.line.name ?? "Escolha a linha"}</h1>
        </div>
        <div className="flex items-center gap-2">
          {queue.items.length > 0 && (
            <span data-testid="pending-count" className="rounded-full border-2 border-[#b45309] bg-[#fef3c7] px-3 py-1 text-[1rem] font-semibold text-[#78350f]">
              {queue.items.length} pendente{queue.items.length > 1 ? "s" : ""} de envio
            </span>
          )}
          <span className="hidden text-[1rem] text-fg-muted sm:inline">{user?.name}</span>
          {confirmLogout ? (
            <button type="button" onClick={signOut} data-testid="op-logout-confirm"
              className="min-h-14 rounded-xl border-2 border-[#7f1d1d] bg-[#b91c1c] px-4 text-[1rem] font-semibold text-white">
              {queue.items.length > 0 ? `Sair com ${queue.items.length} pendente${queue.items.length > 1 ? "s" : ""} (só envia quando você voltar)` : "Sair agora"}
            </button>
          ) : (
            <button type="button" onClick={() => { setConfirmLogout(true); setTimeout(() => setConfirmLogout(false), 5000); }} data-testid="op-logout"
              className="min-h-14 rounded-xl border-2 border-line-strong px-4 text-[1rem] font-semibold text-fg">Trocar operador</button>
          )}
          <button type="button" aria-label="Tela cheia" onClick={() => void document.documentElement.requestFullscreen?.().catch(() => undefined)}
            className="hidden min-h-14 min-w-14 items-center justify-center rounded-xl border-2 border-line-strong text-fg sm:flex">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-6" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /></svg>
          </button>
        </div>
      </header>
      {offline && (
        <div role="alert" data-testid="op-offline" className="border-b-2 border-[#b45309] bg-[#fef3c7] px-4 py-2 text-center text-[1.05rem] font-semibold text-[#78350f]">
          Sem conexão desde {clockTime(conn.since ?? now, current?.plant.timezone ?? "America/Sao_Paulo").slice(0, 5)} — o estado das máquinas não é ao vivo; suas ações ficam guardadas e são enviadas quando a rede voltar.
        </div>
      )}
      {notice && (
        <div role="status" className={clsx("px-4 py-3 text-center text-[1.05rem] font-semibold",
          notice.tone === "ok" ? "bg-[#15803d] text-white" : notice.tone === "warn" ? "bg-[#fef3c7] text-[#78350f]" : "bg-[#b91c1c] text-white")}>
          {notice.text}
        </div>
      )}
      <main className="flex-1 p-4 sm:p-6">
        {tree.isPending ? <p className="text-fg-muted">Carregando…</p>
          : tree.isError ? <p className="text-danger">Não foi possível carregar as linhas.</p>
          : !current ? <LinePicker options={options} onChoose={choose} />
          : (
            <Routes>
              <Route index element={<Home line={current.line} plantId={current.plant.id} tz={current.plant.timezone} dimmed={offline} now={now} onChangeLine={() => choose(null)} pending={queue.items.length} />} />
              <Route path="justify" element={<JustifyList line={current.line} tz={current.plant.timezone} now={now} pendingIds={new Set(queue.items.filter((i) => i.kind === "justify").map((i) => i.event_id ?? ""))} />} />
              <Route path="justify/:eventId" element={<JustifyReasons line={current.line} tz={current.plant.timezone} owner={queue.owner} onQueued={() => void queue.flush()} setNotice={setNotice} />} />
              <Route path="stop" element={<ManualStop line={current.line} owner={queue.owner} onQueued={() => void queue.flush()} setNotice={setNotice} />} />
              <Route path="product" element={<ProductChange line={current.line} setNotice={setNotice} />} />
              <Route path="*" element={<Navigate to="/op" replace />} />
            </Routes>
          )}
        {queue.items.length > 0 && (
          <section aria-label="Pendentes de envio" className="mt-6 rounded-2xl border-2 border-dashed border-[#b45309] p-4">
            <h2 className="text-[1.1rem] font-semibold">Pendentes de envio</h2>
            <ul className="mt-2 space-y-1 text-[1rem] text-fg-muted">
              {queue.items.map((i) => (
                <li key={i.id} className="flex items-center justify-between gap-2">
                  <span>{i.label}{i.not_before && i.not_before > now ? ` — envia em ${Math.ceil((i.not_before - now) / 1000)} s` : " — aguardando rede"}</span>
                  {i.not_before && i.not_before > now && (
                    <button type="button" onClick={() => void cancel(i.id)} className="min-h-12 rounded-xl border-2 border-line-strong px-4 font-semibold text-fg" data-testid="undo">Desfazer</button>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>
    </div>
  );
}

function LinePicker({ options, onChoose }: { options: { plant: { name: string }; line: TreeLine }[]; onChoose: (id: string) => void }) {
  if (options.length === 0) {
    return <p className="text-[1.1rem] text-fg-muted">Nenhuma linha disponível para o seu usuário. Peça ao supervisor para atribuir sua linha.</p>;
  }
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <h2 className="text-[1.4rem] font-bold">Este tablet fica em qual linha?</h2>
      <p className="text-fg-muted">A escolha fica guardada neste aparelho.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {options.map((o) => (
          <BigButton key={o.line.id} onClick={() => onChoose(o.line.id)} testId="op-line">{o.line.name}<span className="text-[1rem] font-normal text-fg-muted">· {o.plant.name}</span></BigButton>
        ))}
      </div>
    </div>
  );
}

function useLineTarget(lineId: string) {
  return useQuery({
    queryKey: ["reports", "line-targets", lineId],
    queryFn: () => api.get<{ line_id: string; parts_per_shift: number | null; oee: number | null; valid_from: string }[]>("/api/reports/line-targets", { params: { line_id: lineId } }).then((r) => r.data),
    staleTime: 10 * 60_000,
    retry: false,
  });
}

function Home({ line, plantId, tz, dimmed, now, onChangeLine, pending }: { line: TreeLine; plantId: string; tz: string; dimmed: boolean; now: number; onChangeLine: () => void; pending: number }) {
  const map = usePlantMap(plantId);
  const unjustified = useDowntime({ line_id: line.id, unjustified: true }, 30_000);
  const target = useLineTarget(line.id);
  const machines: MachineVisual[] = useMemo(
    () => (map.view?.machines ?? []).filter((m) => m.line_id === line.id).sort(bySeverity),
    [map.view, line.id],
  );
  const lv = map.view?.lines.find((l) => l.line_id === line.id);
  const valid = (target.data ?? []).filter((x) => x.valid_from <= new Date(now).toISOString().slice(0, 10));
  const goal = valid.length ? valid[valid.length - 1].parts_per_shift : null;
  const open = (unjustified.data ?? []).filter((e) => e.end == null);
  const count = (unjustified.data ?? []).length;
  const offline = dimmed || map.liveness === "paused";
  const t = now + map.serverOffset;
  return (
    <div className="space-y-5">
      <section aria-label="Turno" className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-2xl border border-line bg-surface p-4">
          <p className="text-[1rem] text-fg-muted">OEE do turno</p>
          <p className="text-[2.5rem] font-bold tabular" data-testid="op-oee">{lv?.kpis?.oee == null ? "—" : formatPct(lv.kpis.oee)}</p>
          {lv?.kpis?.oee == null && map.view && <p className="text-[0.95rem] text-fg-subtle">{map.view.shift ? "sem dados suficientes" : "fora do turno"}</p>}
          {oeeText(lv?.kpis).suspect && <p className="text-[0.95rem] font-semibold text-[#b45309]" title={SUSPECT_TITLE}>⚠ valor suspeito: desempenho acima de 100 %</p>}
        </div>
        <div className="rounded-2xl border border-line bg-surface p-4">
          <p className="text-[1rem] text-fg-muted">Produção do turno{goal ? " / meta" : ""}</p>
          <p className="text-[2.5rem] font-bold tabular">{lv?.kpis ? formatInt(lv.kpis.good) : "—"}{goal ? <span className="text-[1.5rem] text-fg-muted"> / {formatInt(goal)}</span> : null}</p>
          {goal && lv?.kpis && (
            <div className="mt-1 h-3 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
              <div className="h-full rounded-full bg-[#15803d]" style={{ width: `${Math.min(100, (lv.kpis.good / goal) * 100)}%` }} />
            </div>
          )}
        </div>
        <div className="rounded-2xl border border-line bg-surface p-4">
          <p className="text-[1rem] text-fg-muted">{map.view?.shift?.name ?? "Turno"}</p>
          <p className="text-[2.5rem] font-bold tabular">{clockTime(t, tz).slice(0, 5)}</p>
          {pending > 0 && <p className="text-[0.95rem] text-[#b45309]">{pending} ação(ões) aguardando envio</p>}
        </div>
      </section>

      <section aria-label="Máquinas" className={clsx("grid gap-3 sm:grid-cols-2 lg:grid-cols-3 transition-opacity", offline && "opacity-50 grayscale-[0.4]")} data-testid="op-machines">
        {map.query.isPending && (map.query.fetchStatus === "paused"
          ? <p className="text-[#b45309]">Sem conexão: o estado das máquinas aparece quando a rede voltar.</p>
          : <p className="text-fg-muted">Carregando estado das máquinas…</p>)}
        {map.query.isError && <p className="text-danger">Não foi possível carregar o estado das máquinas.</p>}
        {machines.map((m) => {
          const ev = open.find((e) => e.machine_id === m.machine_id);
          return (
            <div key={m.machine_id} data-testid="op-machine" data-state={m.state}
              className={clsx("rounded-2xl border-4 p-4", STATE_META[m.state].marker)}>
              <div className="flex items-center gap-3">
                <StateIcon state={m.state} className="size-9" />
                <div className="min-w-0">
                  <p className="truncate text-[1.35rem] font-bold">{m.name}</p>
                  <p className="text-[1.05rem]">{markerText(m, t)}</p>
                </div>
              </div>
              {ev && <p className="mt-2 text-[2rem] font-bold tabular" aria-label="Tempo de parada">{formatTimer((t - Date.parse(ev.start)) / 1000)}</p>}
            </div>
          );
        })}
      </section>

      <section aria-label="Ações" className="grid gap-3 sm:grid-cols-2">
        <BigButton to="/op/justify" tone={count > 0 ? "warn" : "neutral"} testId="op-justify">
          Justificar paradas{count > 0 ? ` (${count})` : ""}
        </BigButton>
        <BigButton to="/op/stop" testId="op-manual-stop">Declarar parada</BigButton>
        <BigButton to="/op/product" testId="op-product">Trocar produto / ordem</BigButton>
        {open[0] ? (
          <div className="[&_button]:min-h-[4.5rem] [&_button]:w-full [&_button]:rounded-2xl [&_button]:text-[1.25rem]"><CallMaintenanceButton ev={open[0]} size="lg" /></div>
        ) : (
          <BigButton disabled testId="op-call-maint">Chamar manutenção (sem parada aberta)</BigButton>
        )}
      </section>
      <button type="button" onClick={onChangeLine} className="min-h-12 text-[1rem] text-fg-subtle underline">Trocar a linha deste tablet</button>
    </div>
  );
}

function hhmm(iso: string, tz: string): string {
  return new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", timeZone: tz });
}

type QState = { isPending: boolean; fetchStatus: string; isStale?: boolean; dataUpdatedAt: number; data?: unknown };

/** Pending query: "Carregando…" while fetching; offline (paused) with no saved copy says so. */
function Waiting({ q, what }: { q: QState; what: string }) {
  if (q.fetchStatus === "paused") {
    return <p className="rounded-2xl border-2 border-[#b45309] bg-[#fef3c7] p-4 text-[1.1rem] text-[#78350f]" data-testid="op-offline-empty">Sem conexão e sem cópia salva neste tablet: {what} aparecem quando a rede voltar.</p>;
  }
  return <p className="text-fg-muted">Carregando…</p>;
}

/** Offline with data from the device snapshot: say when it was saved (never shown as live). */
function SavedAt({ q, tz }: { q: QState; tz: string }) {
  if (q.data === undefined || q.fetchStatus !== "paused" || !q.dataUpdatedAt) return null;
  return <p className="text-[1rem] text-[#b45309]" data-testid="op-saved-at">Sem conexão — lista salva às {clockTime(q.dataUpdatedAt, tz).slice(0, 5)}; pode já ter mudado.</p>;
}

function JustifyList({ line, tz, now, pendingIds }: { line: TreeLine; tz: string; now: number; pendingIds: Set<string> }) {
  const stops = useDowntime({ line_id: line.id, unjustified: true }, 15_000);
  const reasons = useReasonCodes();
  const list = useMemo(() => (stops.data ?? []).slice().sort((a, b) => Number(b.end == null) - Number(a.end == null) || b.start.localeCompare(a.start)), [stops.data]);
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-[1.4rem] font-bold">Paradas sem motivo</h2>
        <BigButton to="/op" className="!min-h-14 !w-auto px-6 text-[1.1rem]">Voltar</BigButton>
      </div>
      <SavedAt q={stops} tz={tz} />
      {stops.isPending ? <Waiting q={stops} what="as paradas" /> : stops.isError ? <p className="text-danger">{apiErrorMessage(stops.error, "Não foi possível carregar as paradas.")}</p>
        : list.length === 0 ? <p className="rounded-2xl border border-line bg-surface p-6 text-center text-[1.2rem]">Nenhuma parada sem motivo. Bom trabalho!</p> : (
          <ul className="grid gap-3 lg:grid-cols-2" data-testid="op-stops">
            {list.map((e) => (
              <li key={e.id}>
                <Link to={`/op/justify/${e.id}`} data-testid="op-stop"
                  className={clsx("flex min-h-[5.5rem] items-center justify-between gap-4 rounded-2xl border-2 bg-surface p-4", e.end == null ? "border-[#b45309]" : "border-line-strong")}>
                  <span className="min-w-0">
                    <span className="block truncate text-[1.3rem] font-bold">{e.machine_name ?? "Máquina"}</span>
                    <span className="block text-[1rem] text-fg-muted">
                      {hhmm(e.start, tz)}
                      {e.suggested_reason_code ? ` · sugestão: ${reasonLabel(e.suggested_reason_code, reasons.data)}` : ""}
                    </span>
                    {pendingIds.has(e.id) && <span className="mt-1 inline-block rounded-full bg-[#fef3c7] px-2 text-[0.9rem] font-semibold text-[#78350f]">pendente de envio</span>}
                  </span>
                  <span className="shrink-0 text-[1.6rem] font-bold tabular">{e.end ? formatTimer(e.duration_s ?? 0) : formatTimer((now - Date.parse(e.start)) / 1000)}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
    </div>
  );
}

function useMachineHistory(machineId: string | undefined) {
  return useQuery({
    queryKey: ["downtime", { machine_id: machineId, history: 30 }],
    queryFn: () => api.get<DowntimeEvent[]>("/api/downtime/", {
      params: { machine_id: machineId, start: new Date(Date.now() - 30 * 86_400_000).toISOString(), limit: 500 },
    }).then((r) => r.data),
    enabled: !!machineId,
    staleTime: 10 * 60_000,
  });
}

function JustifyReasons({ line, tz, owner, onQueued, setNotice }: { line: TreeLine; tz: string; owner: string; onQueued: () => void; setNotice: (n: { tone: "ok" | "warn" | "danger"; text: string }) => void }) {
  const { eventId = "" } = useParams();
  const navigate = useNavigate();
  const stops = useDowntime({ line_id: line.id, unjustified: true }, 15_000);
  const ev = stops.data?.find((e) => e.id === eventId);
  const reasons = useReasonCodes();
  const hist = useMachineHistory(ev?.machine_id);
  const [all, setAll] = useState(false);
  const active = (reasons.data ?? []).filter((r) => r.is_active && r.code !== "UNK-01");
  const top = useMemo(() => {
    const t = topReasons(hist.data ?? [], 8, (c) => c.startsWith("PLN"));
    const fill = active.filter((r) => !r.planned && !t.includes(r.code)).map((r) => r.code);
    return [...t, ...fill].slice(0, 8);
  }, [hist.data, active]);

  async function choose(code: string) {
    if (!ev) return;
    const label = `${ev.machine_name ?? "Máquina"} — ${reasonLabel(code, reasons.data)}`;
    await enqueue({ kind: "justify", owner, owner_name: useAuthStore.getState().user?.name ?? null, event_id: ev.id, body: { reason_code: code }, label, not_before: Date.now() + UNDO_MS });
    setNotice({ tone: "ok", text: `${label}. Envia em 5 s — toque em Desfazer se errou.` });
    onQueued();
    navigate("/op", { replace: true });
  }

  // Never block on a network-only query: offline with no snapshot says so instead of "Carregando…" forever.
  if (stops.isPending) return <Waiting q={stops} what="as paradas" />;
  if (reasons.isPending && reasons.fetchStatus !== "paused") return <p className="text-fg-muted">Carregando…</p>;
  if (!ev) return (
    <div className="space-y-4">
      <p className="text-[1.2rem]">Esta parada já foi justificada (ou não existe mais).</p>
      <BigButton to="/op/justify">Voltar às paradas</BigButton>
    </div>
  );
  const byCat = new Map<string, ReasonCode[]>();
  for (const r of active) byCat.set(r.category, [...(byCat.get(r.category) ?? []), r]);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-[1.4rem] font-bold">{ev.machine_name} — qual foi o motivo?</h2>
          <p className="text-fg-muted">Parou às {hhmm(ev.start, tz)}</p>
        </div>
        <BigButton to="/op/justify" className="!min-h-14 !w-auto px-6 text-[1.1rem]">Voltar</BigButton>
      </div>
      {ev.suggested_reason_code && (
        <BigButton tone="accent" onClick={() => void choose(ev.suggested_reason_code as string)} testId="op-confirm-suggested">
          Confirmar: {reasonLabel(ev.suggested_reason_code, reasons.data)}
        </BigButton>
      )}
      {reasons.data === undefined && (
        <p className="rounded-2xl border-2 border-[#b45309] bg-[#fef3c7] p-4 text-[1.05rem] text-[#78350f]" data-testid="op-reasons-offline">
          Sem conexão: a lista de motivos ainda não foi baixada neste tablet. Use a sugestão (se houver) ou justifique quando a rede voltar.
        </p>
      )}
      {!all ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="op-top-reasons">
          {top.map((c) => (
            <BigButton key={c} onClick={() => void choose(c)} testId="op-reason">{reasonLabel(c, reasons.data)}</BigButton>
          ))}
          <BigButton onClick={() => setAll(true)} testId="op-reason-others">Outros…</BigButton>
        </div>
      ) : (
        <div className="space-y-4">
          {[...byCat.entries()].map(([cat, list]) => (
            <section key={cat}>
              <h3 className="mb-2 text-[1.1rem] font-semibold text-fg-muted">{reasonLabel(cat, undefined)}</h3>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                {list.map((r) => <BigButton key={r.code} onClick={() => void choose(r.code)}>{r.label}</BigButton>)}
              </div>
            </section>
          ))}
          <BigButton onClick={() => setAll(false)}>Mais usados</BigButton>
        </div>
      )}
    </div>
  );
}

const AGO = [0, 5, 10, 15, 30];

function ManualStop({ line, owner, onQueued, setNotice }: { line: TreeLine; owner: string; onQueued: () => void; setNotice: (n: { tone: "ok" | "warn" | "danger"; text: string }) => void }) {
  const navigate = useNavigate();
  const reasons = useReasonCodes();
  const [machine, setMachine] = useState<string | null>(line.machines.length === 1 ? line.machines[0].id : null);
  const [ago, setAgo] = useState(0);
  const [ongoing, setOngoing] = useState(true);
  const active = (reasons.data ?? []).filter((r) => r.is_active && r.code !== "UNK-01");
  async function choose(code: string) {
    if (!machine) return;
    const now = Date.now();
    const start = new Date(now - ago * 60_000).toISOString();
    const m = line.machines.find((x) => x.id === machine);
    const label = `Parada declarada: ${m?.name ?? "máquina"} — ${reasonLabel(code, reasons.data)}`;
    await enqueue({ kind: "create_stop", owner, owner_name: useAuthStore.getState().user?.name ?? null, body: { machine_id: machine, start, end: ongoing ? null : new Date(now).toISOString(), reason_code: code }, label, not_before: now + UNDO_MS });
    setNotice({ tone: "ok", text: `${label}. Envia em 5 s — toque em Desfazer se errou.` });
    onQueued();
    navigate("/op", { replace: true });
  }
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-[1.4rem] font-bold">Declarar parada</h2>
        <BigButton to="/op" className="!min-h-14 !w-auto px-6 text-[1.1rem]">Voltar</BigButton>
      </div>
      <section>
        <h3 className="mb-2 text-[1.1rem] font-semibold text-fg-muted">1. Máquina</h3>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {line.machines.map((m) => (
            <BigButton key={m.id} tone={machine === m.id ? "accent" : "neutral"} onClick={() => setMachine(m.id)}>{m.name}</BigButton>
          ))}
        </div>
      </section>
      <section>
        <h3 className="mb-2 text-[1.1rem] font-semibold text-fg-muted">2. Quando começou</h3>
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-5">
          {AGO.map((a) => <BigButton key={a} tone={ago === a ? "accent" : "neutral"} onClick={() => setAgo(a)}>{a === 0 ? "Agora" : `Há ${a} min`}</BigButton>)}
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <BigButton tone={ongoing ? "accent" : "neutral"} onClick={() => setOngoing(true)}>Ainda parada</BigButton>
          <BigButton tone={!ongoing ? "accent" : "neutral"} onClick={() => setOngoing(false)}>Já voltou (fim agora)</BigButton>
        </div>
      </section>
      <section>
        <h3 className="mb-2 text-[1.1rem] font-semibold text-fg-muted">3. Motivo</h3>
        {!machine ? <p className="text-fg-muted">Escolha a máquina primeiro.</p> : (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {active.map((r) => <BigButton key={r.code} onClick={() => void choose(r.code)}>{r.label}</BigButton>)}
          </div>
        )}
      </section>
    </div>
  );
}

function ProductChange({ line, setNotice }: { line: TreeLine; setNotice: (n: { tone: "ok" | "warn" | "danger"; text: string }) => void }) {
  const navigate = useNavigate();
  const [machine, setMachine] = useState<string | null>(line.output_machine_id ?? (line.machines.length === 1 ? line.machines[0].id : null));
  const products = useMachineProducts(machine);
  const run = useOpenRun(machine);
  const start = useStartRun();
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-[1.4rem] font-bold">Trocar produto / ordem</h2>
        <BigButton to="/op" className="!min-h-14 !w-auto px-6 text-[1.1rem]">Voltar</BigButton>
      </div>
      <p className="text-fg-muted">A ordem nova começa no início do setup (S3-3).</p>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {line.machines.map((m) => <BigButton key={m.id} tone={machine === m.id ? "accent" : "neutral"} onClick={() => setMachine(m.id)}>{m.name}</BigButton>)}
      </div>
      {machine && (
        products.isPending ? <p className="text-fg-muted">Carregando produtos…</p> : (products.data ?? []).length === 0 ? (
          <p className="text-fg-muted">Nenhum produto vinculado a esta máquina — peça ao gestor.</p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {(products.data ?? []).map((p) => {
              const cur = run.data?.product_id === p.product_id;
              return (
                <BigButton key={p.id} tone={cur ? "accent" : "neutral"} disabled={cur || start.isPending}
                  onClick={() => start.mutate({ machine_id: machine, product_id: p.product_id }, {
                    onSuccess: () => { setNotice({ tone: "ok", text: `Produção de ${p.product_name ?? p.product_code} iniciada.` }); navigate("/op", { replace: true }); },
                    onError: (e) => setNotice({ tone: "danger", text: apiErrorMessage(e, "Não foi possível trocar o produto.") }),
                  })}>
                  {p.product_name ?? p.product_code}{cur ? " (atual)" : ""}
                </BigButton>
              );
            })}
          </div>
        )
      )}
    </div>
  );
}

/** Kiosk login of the tablet (outside the app chrome). */
export function OperatorLoginPage() {
  const login = useAuthStore((s) => s.login);
  const loading = useAuthStore((s) => s.loading);
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { if (user) navigate("/op", { replace: true }); }, [user, navigate]);
  useEffect(() => { void registerServiceWorker(); }, []);
  const waiting = usePendingOwners();
  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-canvas p-4 text-[18px] text-fg" data-testid="op-login">
      <form className="w-full max-w-md space-y-4 rounded-2xl border border-line bg-surface p-6 shadow-card"
        onSubmit={(e) => {
          e.preventDefault();
          setError("");
          login(email.trim(), password).then(() => navigate((location.state as { from?: string } | null)?.from ?? "/op", { replace: true }))
            .catch((err) => setError(apiErrorMessage(err, "Não foi possível entrar. Confira usuário e senha.")));
        }}>
        <h1 className="text-[1.6rem] font-bold">Tablet do operador</h1>
        {waiting.length > 0 && (
          <div role="status" data-testid="op-login-pending" className="rounded-xl border-2 border-[#b45309] bg-[#fef3c7] p-3 text-[1rem] text-[#78350f]">
            {waiting.map((w) => (
              <p key={w.owner}>{w.count} {w.count > 1 ? "ações pendentes" : "ação pendente"} de {w.owner_name ?? "outro operador"} — envia quando {w.owner_name ? "ele(a)" : "esse operador"} entrar neste tablet.</p>
            ))}
          </div>
        )}
        <label className="block">
          <span className="text-[1rem] text-fg-muted">Usuário (e-mail)</span>
          <input value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required
            className="mt-1 min-h-14 w-full rounded-xl border-2 border-line-strong bg-surface px-4 text-[1.2rem] text-fg focus:border-accent focus:outline-none" />
        </label>
        <label className="block">
          <span className="text-[1rem] text-fg-muted">Senha</span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required
            className="mt-1 min-h-14 w-full rounded-xl border-2 border-line-strong bg-surface px-4 text-[1.2rem] text-fg focus:border-accent focus:outline-none" />
        </label>
        {error && <p role="alert" className="text-[1rem] text-danger">{error}</p>}
        <button type="submit" disabled={loading} className="min-h-16 w-full rounded-2xl bg-[#15803d] text-[1.3rem] font-bold text-white disabled:opacity-60">
          {loading ? "Entrando…" : "Entrar"}
        </button>
      </form>
    </div>
  );
}
