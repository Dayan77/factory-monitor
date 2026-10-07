import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import clsx from "clsx";
import ExportMenu from "@/components/reports/ExportMenu";
import { localDate } from "@/utils/period";
import Button from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import Badge from "@/components/ui/Badge";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import Sheet from "@/components/ui/Sheet";
import { Field, inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import ReasonPicker from "@/components/efficiency/ReasonPicker";
import CallMaintenanceButton from "@/components/maintenance/CallMaintenanceButton";
import {
  useCreateDowntime, useDowntime, useJustifyDowntime, useMachineProducts, useOpenRun, usePlant, useReasonCodes,
  useStartRun, useStopRun,
} from "@/hooks/useEfficiency";
import { useNow } from "@/hooks/useNow";
import type { DowntimeEvent, TreeLine } from "@/services/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";
import { formatDuration, formatTimer, groupReasonCodes, reasonLabel } from "@/utils/efficiency";

const SOURCE_SHORT: Record<string, string> = {
  auto_fault: "sinal de falha", auto_run_state: "máquina parada", auto_cycle_gap: "sem ciclos", manual: "manual",
};

/** "2026-10-05T14:30" (datetime-local, browser time) ↔ ISO. */
function toLocalInput(ms: number): string {
  const d = new Date(ms - new Date(ms).getTimezoneOffset() * 60_000);
  return d.toISOString().slice(0, 16);
}

function StopCard({ ev, now, onOpen, reasons }: { ev: DowntimeEvent; now: number; onOpen: () => void; reasons: Parameters<typeof reasonLabel>[1] }) {
  const open = ev.end == null;
  const secs = open ? (now - Date.parse(ev.start)) / 1000 : ev.duration_s;
  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid="stop-card"
      className={clsx(
        "flex w-full min-h-20 items-center gap-4 rounded-2xl border-2 bg-surface p-4 text-left shadow-card transition-colors",
        open ? "border-danger-solid/50 hover:bg-danger-solid/5" : "border-line hover:border-line-strong hover:bg-surface-2/60",
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate text-heading text-fg">{ev.machine_name ?? "Máquina"}</span>
          {open && <Badge tone="danger" dot pulse>Parada agora</Badge>}
        </div>
        <p className="mt-0.5 text-caption text-fg-subtle">
          {formatDateTime(ev.start)} · {SOURCE_SHORT[ev.source] ?? ev.source}
          {ev.fault_code != null && ` · código ${ev.fault_code}`}
        </p>
        {ev.suggested_reason_code && (
          <p className="mt-1 text-caption text-info">Sugestão: {reasonLabel(ev.suggested_reason_code, reasons)}</p>
        )}
      </div>
      <div className="shrink-0 text-right">
        <p className={clsx("text-title tabular", open ? "text-danger" : "text-fg")}>{open ? formatTimer(secs) : formatDuration(secs)}</p>
        <p className="text-caption font-medium text-ok">Justificar ›</p>
      </div>
    </button>
  );
}

/** Reason preselected when a stop is opened: the detector's suggestion (never UNK-01). */
function initialCode(ev: DowntimeEvent | null): string | null {
  const s = ev?.suggested_reason_code;
  return s && s !== "UNK-01" ? s : null;
}

function JustifySheet({ ev, onClose, onDone }: { ev: DowntimeEvent | null; onClose: () => void; onDone: (msg: string) => void }) {
  const reasons = useReasonCodes();
  const groups = useMemo(() => groupReasonCodes(reasons.data ?? []), [reasons.data]);
  const [code, setCode] = useState<string | null>(() => initialCode(ev));
  const [comment, setComment] = useState(() => ev?.comment ?? "");
  const [forId, setForId] = useState(ev?.id ?? null);
  const justify = useJustifyDowntime();

  // A new stop opened in the (still mounted) sheet: reset while rendering — React re-renders before
  // committing, so the first paint never shows the previous stop's reason (a useEffect reset would).
  const evId = ev?.id ?? null;
  if (evId !== forId) {
    setForId(evId);
    if (ev) {
      setCode(initialCode(ev));
      setComment(ev.comment ?? "");
    }
  }
  useEffect(() => {
    if (evId) justify.reset();
  }, [evId]); // eslint-disable-line react-hooks/exhaustive-deps

  function submit() {
    if (!ev || !code) return;
    justify.mutate(
      { id: ev.id, body: { reason_code: code, comment: comment.trim() || null } },
      { onSuccess: () => { onDone(`Parada de ${ev.machine_name ?? "máquina"} justificada: ${reasonLabel(code, reasons.data)}.`); onClose(); } },
    );
  }

  return (
    <Sheet open={!!ev} onClose={onClose} size="lg" title="Justificar parada"
      description={ev ? `${ev.machine_name ?? ""} · ${formatDateTime(ev.start)} · ${ev.end ? formatDuration(ev.duration_s) : "em andamento"}` : undefined}
      footer={
        <div className="space-y-2">
          {justify.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(justify.error, "Não foi possível salvar. Tente de novo.")}</p>}
          <div className="flex gap-2">
            <Button size="lg" variant="ghost" onClick={onClose} className="min-h-14 flex-1">Cancelar</Button>
            <Button size="lg" variant="primary" onClick={submit} disabled={!code} loading={justify.isPending} className="min-h-14 flex-[2] text-heading">
              Confirmar motivo
            </Button>
          </div>
        </div>
      }>
      {reasons.isPending ? <Skeleton className="h-64" /> : reasons.isError ? (
        <p role="alert" className="text-danger">Não foi possível carregar os motivos.</p>
      ) : (
        <div className="space-y-5">
          {ev && ev.end == null && <CallMaintenanceButton ev={ev} />}
          <ReasonPicker groups={groups} value={code} suggested={ev?.suggested_reason_code} onChange={setCode} />
          <Field label="Comentário (opcional)" htmlFor="stop-comment">
            <textarea id="stop-comment" rows={3} value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000}
              className={clsx(inputClass, "text-heading")} placeholder="Ex.: sensor do cilindro 2 trocado" />
          </Field>
        </div>
      )}
    </Sheet>
  );
}

function MachineSelect({ lines, value, onChange, id }: { lines: TreeLine[]; value: string; onChange: (v: string) => void; id: string }) {
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={clsx(inputClass, "min-h-14 text-heading")}>
      <option value="">Escolha a máquina…</option>
      {lines.map((l) => (
        <optgroup key={l.id} label={l.name}>
          {l.machines.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </optgroup>
      ))}
    </select>
  );
}

function ManualStopSheet({ open, onClose, lines, onDone }: { open: boolean; onClose: () => void; lines: TreeLine[]; onDone: (m: string) => void }) {
  const reasons = useReasonCodes();
  const groups = useMemo(() => groupReasonCodes(reasons.data ?? []), [reasons.data]);
  const create = useCreateDowntime();
  const [machine, setMachine] = useState("");
  const [start, setStart] = useState("");
  const [ongoing, setOngoing] = useState(true);
  const [end, setEnd] = useState("");
  const [code, setCode] = useState<string | null>(null);
  const [comment, setComment] = useState("");

  useEffect(() => {
    if (!open) return;
    const now = Date.now();
    setMachine(lines.length === 1 && lines[0].machines.length === 1 ? lines[0].machines[0].id : "");
    setStart(toLocalInput(now - 10 * 60_000));
    setEnd(toLocalInput(now));
    setOngoing(true);
    setCode(null);
    setComment("");
    create.reset();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const startMs = Date.parse(start);
  const endMs = Date.parse(end);
  const invalid = !machine || !code || Number.isNaN(startMs) || startMs > Date.now() || (!ongoing && (Number.isNaN(endMs) || endMs <= startMs));

  function submit() {
    if (invalid || !code) return;
    create.mutate(
      { machine_id: machine, start: new Date(startMs).toISOString(), end: ongoing ? null : new Date(endMs).toISOString(), reason_code: code, comment: comment.trim() || null },
      { onSuccess: () => { onDone("Parada registrada."); onClose(); } },
    );
  }

  return (
    <Sheet open={open} onClose={onClose} size="lg" title="Registrar parada" description="Para paradas que o sistema não detectou (ou durante falta de dados)."
      footer={
        <div className="space-y-2">
          {create.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(create.error, "Não foi possível registrar.")}</p>}
          <Button size="lg" variant="primary" className="min-h-14 w-full text-heading" disabled={invalid} loading={create.isPending} onClick={submit}>Registrar parada</Button>
        </div>
      }>
      <div className="space-y-5">
        <Field label="Máquina" htmlFor="ms-machine"><MachineSelect id="ms-machine" lines={lines} value={machine} onChange={setMachine} /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Início" htmlFor="ms-start">
            <input id="ms-start" type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} className={clsx(inputClass, "min-h-12")} />
          </Field>
          <Field label="Fim" htmlFor="ms-end" hint={ongoing ? "A parada fica aberta até a máquina voltar." : undefined}>
            <input id="ms-end" type="datetime-local" value={end} disabled={ongoing} onChange={(e) => setEnd(e.target.value)} className={clsx(inputClass, "min-h-12")} />
          </Field>
        </div>
        <label className="flex min-h-12 cursor-pointer items-center gap-3 rounded-xl border border-line px-4">
          <input type="checkbox" checked={ongoing} onChange={(e) => setOngoing(e.target.checked)} className="size-5 accent-[rgb(var(--c-accent))]" />
          <span className="text-body text-fg">Ainda parada (em andamento)</span>
        </label>
        {reasons.isPending ? <Skeleton className="h-48" /> : <ReasonPicker groups={groups} value={code} onChange={setCode} />}
        <Field label="Comentário (opcional)" htmlFor="ms-comment">
          <textarea id="ms-comment" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000} className={inputClass} />
        </Field>
      </div>
    </Sheet>
  );
}

function ProductSheet({ open, onClose, lines, onDone }: { open: boolean; onClose: () => void; lines: TreeLine[]; onDone: (m: string) => void }) {
  const [machine, setMachine] = useState("");
  const products = useMachineProducts(open && machine ? machine : null);
  const run = useOpenRun(open && machine ? machine : null);
  const start = useStartRun();
  const stop = useStopRun();

  useEffect(() => {
    if (open) { setMachine(""); start.reset(); stop.reset(); }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const current = run.data;
  const err = start.error ?? stop.error;
  return (
    <Sheet open={open} onClose={onClose} size="lg" title="Trocar produto" description="Inicia a produção do produto escolhido (encerra a atual).">
      <div className="space-y-5">
        <Field label="Máquina" htmlFor="pr-machine"><MachineSelect id="pr-machine" lines={lines} value={machine} onChange={setMachine} /></Field>
        {machine && (
          <>
            <div className="rounded-xl border border-line bg-surface-2/50 p-4">
              <p className="text-caption text-fg-subtle">Produzindo agora</p>
              {run.isPending ? <Skeleton className="mt-1 h-6 w-40" /> : current ? (
                <div className="mt-1 flex flex-wrap items-center justify-between gap-3">
                  <p className="text-heading text-fg">
                    {products.data?.find((p) => p.product_id === current.product_id)?.product_name ?? current.product_code ?? "Produto"}
                    <span className="ml-2 text-caption text-fg-subtle">desde {formatDateTime(current.started_at)}</span>
                  </p>
                  <Button variant="danger-ghost" size="lg" loading={stop.isPending}
                    onClick={() => stop.mutate({ runId: current.id, machineId: machine }, { onSuccess: () => onDone("Produção encerrada.") })}>
                    Encerrar produção
                  </Button>
                </div>
              ) : (
                <p className="mt-1 text-heading text-warn">Produto não informado</p>
              )}
            </div>
            <QueryView query={products} loading={<Skeleton className="h-32" />} errorTitle="Não foi possível carregar os produtos"
              isEmpty={(d) => d.length === 0}
              empty={<EmptyState title="Nenhum produto vinculado a esta máquina" description="Peça ao gestor para cadastrar em Configurações → Produtos." />}>
              {(list) => (
                <ul className="grid gap-2 sm:grid-cols-2" aria-label="Produtos">
                  {list.map((p) => {
                    const isCurrent = current?.product_id === p.product_id;
                    return (
                      <li key={p.id}><button type="button" disabled={isCurrent || start.isPending}
                        onClick={() => start.mutate({ machine_id: machine, product_id: p.product_id }, { onSuccess: () => { onDone(`Produção de ${p.product_name ?? p.product_code} iniciada.`); onClose(); } })}
                        className={clsx("flex min-h-16 w-full flex-col justify-center rounded-xl border-2 px-4 py-2 text-left transition-colors disabled:cursor-default",
                          isCurrent ? "border-accent bg-accent/10" : "border-line hover:border-line-strong hover:bg-surface-2")}>
                        <span className="text-body font-medium text-fg">{p.product_name ?? p.product_code}</span>
                        <span className="text-caption text-fg-subtle">{p.product_code} · ciclo ideal {p.ideal_cycle_time_s.toString().replace(".", ",")} s{isCurrent ? " · atual" : ""}</span>
                      </button></li>
                    );
                  })}
                </ul>
              )}
            </QueryView>
            {err && <p role="alert" className="text-caption text-danger">{apiErrorMessage(err, "Não foi possível trocar o produto.")}</p>}
          </>
        )}
      </div>
    </Sheet>
  );
}

/** Operator tablet: unjustified stops (live), justify with one hand, manual stop, product change. */
export default function StopsPage() {
  const { tree, plant } = usePlant();
  const [params, setParams] = useSearchParams();
  const lineId = params.get("line") ?? "";
  const lines = plant?.lines ?? [];
  const scopedLines = lineId ? lines.filter((l) => l.id === lineId) : lines;
  const stops = useDowntime({ unjustified: true, ...(lineId ? { line_id: lineId } : {}) }, 30_000);
  const reasons = useReasonCodes();
  const now = useNow(1000);
  const [selected, setSelected] = useState<DowntimeEvent | null>(null);
  const [sheet, setSheet] = useState<"manual" | "product" | null>(null);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(""), 5000);
    return () => clearTimeout(t);
  }, [notice]);

  const list = useMemo(
    () => (stops.data ?? []).slice().sort((a, b) => Number(b.end == null) - Number(a.end == null) || b.start.localeCompare(a.start)),
    [stops.data],
  );

  function chooseLine(id: string) {
    const next = new URLSearchParams(params);
    if (id) next.set("line", id); else next.delete("line");
    setParams(next, { replace: true });
  }

  return (
    <div className="mx-auto max-w-4xl space-y-5 animate-rise-in">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-title text-fg">Justificar paradas</h2>
          <p className="text-caption text-fg-subtle">Toque numa parada e escolha o motivo. A lista atualiza sozinha.</p>
        </div>
        <div className="grid w-full grid-cols-2 gap-2 sm:w-auto sm:grid-cols-[auto_auto_auto]">
          {plant && (
            <ExportMenu report="daily_production" scopeType="plant" scopeId={plant.id} label="Exportar paradas do dia"
              className="col-span-2 sm:col-span-1 [&>button]:min-h-14 [&>button]:w-full" params={{ productive_day: localDate(Date.now(), plant.timezone) }} />
          )}
          <Button size="lg" variant="secondary" className="min-h-14" onClick={() => setSheet("product")}>Trocar produto</Button>
          <Button size="lg" variant="secondary" className="min-h-14" onClick={() => setSheet("manual")}>Registrar parada</Button>
        </div>
      </div>

      {lines.length > 1 && (
        <div role="group" aria-label="Filtrar por linha" className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
          {[{ id: "", name: "Todas" }, ...lines].map((l) => (
            <button key={l.id} type="button" aria-pressed={lineId === l.id} onClick={() => chooseLine(l.id)}
              className={clsx("min-h-12 shrink-0 rounded-full border px-5 text-body font-medium transition-colors",
                lineId === l.id ? "border-accent bg-accent/15 text-ok" : "border-line bg-surface text-fg-muted hover:bg-surface-2")}>
              {l.name}
            </button>
          ))}
        </div>
      )}

      <p aria-live="polite" className={clsx("rounded-xl bg-ok-solid/15 px-4 py-3 text-body font-medium text-ok", !notice && "sr-only")}>{notice}</p>

      <section aria-label="Paradas sem justificativa">
        {tree.isPending ? <Skeleton className="h-20 rounded-2xl" /> : (
          <QueryView query={stops} errorTitle="Não foi possível carregar as paradas"
            loading={<LoadingRegion label="Carregando paradas…"><div className="space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-20 rounded-2xl" />)}</div></LoadingRegion>}
            isEmpty={() => list.length === 0}
            empty={<Card><EmptyState title="Nenhuma parada sem justificativa" description="Bom trabalho! Novas paradas aparecem aqui automaticamente." /></Card>}>
            {() => (
              <>
                <p className="mb-2 text-caption font-medium text-fg-muted">{list.length} parada{list.length > 1 ? "s" : ""} sem justificativa</p>
                <ul className="space-y-3">
                  {list.map((ev) => (
                    <li key={ev.id} className="space-y-2">
                      <StopCard ev={ev} now={now} reasons={reasons.data} onOpen={() => setSelected(ev)} />
                      {ev.end == null && <CallMaintenanceButton ev={ev} size="md" />}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </QueryView>
        )}
      </section>

      <JustifySheet ev={selected} onClose={() => setSelected(null)} onDone={setNotice} />
      <ManualStopSheet open={sheet === "manual"} onClose={() => setSheet(null)} lines={scopedLines} onDone={setNotice} />
      <ProductSheet open={sheet === "product"} onClose={() => setSheet(null)} lines={scopedLines} onDone={setNotice} />
    </div>
  );
}
