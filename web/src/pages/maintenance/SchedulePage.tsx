import { useMemo, useState, type DragEvent } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Card, CardHeader } from "@/components/ui/Card";
import Button from "@/components/ui/Button";
import Badge from "@/components/ui/Badge";
import Stat from "@/components/ui/Stat";
import Sheet from "@/components/ui/Sheet";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Progress } from "@/components/ui/LifeBar";
import { BreakInBadge, OverdueBadge, WoTypeBadge } from "@/components/maintenance/badges";
import WorkOrderSheet, { TransitionForm } from "@/components/maintenance/WorkOrderSheet";
import { transitionErrorMessage } from "@/components/maintenance/transitionError";
import { useMaintenanceKpis, useTechnicians, useTransition, useUpdateWorkOrder, useWoNames, useWorkOrders } from "@/hooks/useMaintenance";
import { usePlant } from "@/hooks/useEfficiency";
import { calendarApi } from "@/services/api";
import type { TransitionBody, WorkOrder } from "@/services/maintenanceTypes";
import { formatDate, formatDec1 } from "@/utils/format";
import {
  addDays, dayOf, hoursLabel, isBreakIn, isoDay, isWoOverdue, mondayOf, remainingHours,
} from "@/utils/maintenance";
import { useUrlFilters } from "./filters";

const WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];
const UNASSIGNED = "__none__";

function dayLabel(day: string, i: number) {
  const [, m, d] = day.split("-");
  return `${WEEKDAYS[i]} ${d}/${m}`;
}

function MiniCard({ wo, today, onOpen, onPlan, draggable = true }: { wo: WorkOrder; today: string; onOpen: () => void; onPlan?: () => void; draggable?: boolean }) {
  const names = useWoNames();
  function onDragStart(e: DragEvent) {
    e.dataTransfer.setData("text/wo-id", wo.id);
    e.dataTransfer.effectAllowed = "move";
  }
  return (
    <div draggable={draggable} onDragStart={onDragStart} data-testid="schedule-card" data-wo={wo.number}
      className={clsx("rounded-lg border bg-surface p-2 text-left shadow-card", draggable && "cursor-grab active:cursor-grabbing",
        isWoOverdue(wo, today) ? "border-danger-solid/50" : "border-line")}>
      <button type="button" onClick={onOpen} className="block w-full text-left">
        <span className="flex items-center justify-between gap-1 text-caption">
          <span className="whitespace-nowrap font-semibold text-fg-muted">{wo.number}</span>
          <span className="tabular text-fg-subtle">{hoursLabel(remainingHours(wo))}</span>
        </span>
        <span className="mt-0.5 line-clamp-2 block text-caption font-medium text-fg">{wo.title}</span>
        <span className="block truncate text-caption text-fg-subtle">{names.machine(wo)}</span>
      </button>
      <div className="mt-1 flex flex-wrap items-center gap-1">
        {isWoOverdue(wo, today) && <OverdueBadge />}
        {wo.state === "scheduled" && isBreakIn(wo) && <BreakInBadge />}
        {wo.stops_machine && <Badge tone="warn">Para a máquina</Badge>}
        {onPlan && <Button size="sm" variant="ghost" className="ml-auto min-h-8" onClick={onPlan}>Programar…</Button>}
      </div>
    </div>
  );
}

/** Planner: weekly schedule — capacity × backlog, drag approved WOs to a day/technician (workflows §2, 08:00). */
export default function SchedulePage() {
  const { values: f, set } = useUrlFilters(["week", "wo"] as const);
  const { plant } = usePlant();
  const today = isoDay();
  const monday = f.week && /^\d{4}-\d{2}-\d{2}$/.test(f.week) ? mondayOf(f.week) : mondayOf(today);
  const days = Array.from({ length: 7 }, (_, i) => addDays(monday, i));
  const sunday = days[6];
  const wos = useWorkOrders({});
  const kpis = useMaintenanceKpis({ plant_id: plant?.id, start: `${monday}T00:00:00`, stop: `${addDays(monday, 7)}T00:00:00` });
  const techs = useTechnicians();
  const calendar = useQuery({
    queryKey: ["calendar", plant?.id ?? null],
    queryFn: () => calendarApi.list(plant?.id).then((r) => r.data),
    enabled: !!plant,
  });
  const transition = useTransition();
  const update = useUpdateWorkOrder();
  const [planning, setPlanning] = useState<{ wo: WorkOrder; date?: string } | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const [over, setOver] = useState<string | null>(null);

  const all = wos.data ?? [];
  const byId = useMemo(() => new Map(all.map((w) => [w.id, w])), [all]);
  const toPlan = all.filter((w) => w.state === "approved" || w.state === "on_hold");
  const pending = all.filter((w) => w.state === "draft" || w.state === "open");
  const weekWos = all.filter((w) => {
    const d = dayOf(w.scheduled_for);
    return !!d && d >= monday && d <= sunday && ["scheduled", "in_progress", "on_hold", "completed", "closed"].includes(w.state);
  });
  const rows = techs.data?.length
    ? [...techs.data.map((t) => ({ id: t.id, name: t.name })), { id: UNASSIGNED, name: "Sem responsável" }]
    : [{ id: "*", name: "Equipe" }];
  const weekly = kpis.data?.crew_capacity_hours_per_week ?? null;
  const daily = weekly != null ? weekly / 5 : null;
  const scheduledHours = weekWos.reduce((s, w) => s + remainingHours(w), 0);
  const windows = (calendar.data ?? []).filter((c) => c.kind === "planned_stop" && c.date >= monday && c.date <= sunday && (!c.reason_code || c.reason_code === "PLN-03"));
  const frozen = today >= monday;

  async function schedule(wo: WorkOrder, body: TransitionBody) {
    setPlanError(null);
    try {
      await transition.mutateAsync({ id: wo.id, body });
      setPlanning(null);
      setNotice({ tone: "ok", text: `${wo.number} programada para ${formatDate(body.scheduled_for)}${frozen && body.scheduled_for && body.scheduled_for <= sunday ? " (encaixe — semana já congelada)" : ""}.${wo.stops_machine ? " Pedido de janela de parada enviado ao supervisor." : ""}` });
    } catch (err) {
      const msg = transitionErrorMessage(err);
      setPlanError(msg);
      setNotice({ tone: "danger", text: `${wo.number}: ${msg}` });
    }
  }

  function onDrop(e: DragEvent, date: string, assignee: string) {
    e.preventDefault();
    setOver(null);
    const wo = byId.get(e.dataTransfer.getData("text/wo-id"));
    if (!wo) return;
    const assignee_id = assignee === "*" ? wo.assignee_id : assignee === UNASSIGNED ? null : assignee;
    if (wo.state === "scheduled") {
      // reprogramming keeps scheduled_at (frozen week) — S4-5
      update.mutate({ id: wo.id, body: { scheduled_for: date, assignee_id } }, {
        onSuccess: () => setNotice({ tone: "ok", text: `${wo.number} reprogramada para ${formatDate(date)}.` }),
        onError: (err) => setNotice({ tone: "danger", text: `${wo.number}: ${transitionErrorMessage(err)}` }),
      });
      return;
    }
    if (!wo.allowed_transitions.includes("scheduled")) {
      setNotice({ tone: "danger", text: `${wo.number} não pode ser programada no estado atual. Abra a OS para ver as ações permitidas.` });
      return;
    }
    void schedule(wo, { to: "scheduled", scheduled_for: date, assignee_id, note: "programada pelo quadro semanal" });
  }

  function cell(date: string, rowId: string) {
    return weekWos.filter((w) => dayOf(w.scheduled_for) === date && (rowId === "*" || (rowId === UNASSIGNED ? !w.assignee_id : w.assignee_id === rowId)));
  }

  return (
    <div className="mx-auto max-w-[96rem] space-y-5 animate-rise-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-title text-fg">Programação da semana</h2>
          <p className="text-caption text-fg-subtle">Arraste as OS aprovadas para o dia e o técnico. Capacidade da equipe × backlog.</p>
        </div>
        <div className="flex items-center gap-2" role="group" aria-label="Semana">
          <Button variant="secondary" aria-label="Semana anterior" onClick={() => set({ week: addDays(monday, -7) })}>‹</Button>
          <span className="min-w-[11rem] text-center text-body font-medium tabular text-fg" aria-live="polite">{formatDate(monday)} – {formatDate(sunday)}</span>
          <Button variant="secondary" aria-label="Próxima semana" onClick={() => set({ week: addDays(monday, 7) })}>›</Button>
          {monday !== mondayOf(today) && <Button variant="ghost" onClick={() => set({ week: null })}>Hoje</Button>}
        </div>
      </div>

      <section aria-label="Capacidade e backlog" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {kpis.isPending ? Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-28 rounded-xl" />) : kpis.isError ? (
          <div className="col-span-full"><ErrorState title="Não foi possível carregar backlog e capacidade" error={kpis.error} onRetry={() => void kpis.refetch()} /></div>
        ) : kpis.data && (
          <>
            <Stat kpi="backlog" label="Backlog" value={formatDec1(kpis.data.backlog_hours)} unit="h"
              hint={kpis.data.backlog_weeks == null ? "— sem capacidade cadastrada" : `${formatDec1(kpis.data.backlog_weeks)} semana(s) · referência 2–4`}
              tone={kpis.data.backlog_weeks == null ? "neutral" : kpis.data.backlog_weeks > 4 ? "danger" : kpis.data.backlog_weeks < 2 ? "warn" : "ok"} />
            <Stat label="Capacidade da equipe" value={weekly == null ? "—" : formatDec1(weekly)} unit={weekly == null ? undefined : "h/semana"} hint="Técnicos × horas − ausências previstas" />
            <div className="surface rounded-xl border border-line bg-surface p-4 shadow-card">
              <p className="text-caption font-medium text-fg-muted">Programado nesta semana</p>
              <p className="mt-1.5 text-display tabular text-fg">{formatDec1(scheduledHours)}<span className="ml-1 text-body text-fg-subtle">h</span></p>
              {weekly != null && weekly > 0 && (
                <Progress className="mt-2" value={scheduledHours / weekly} label="Programado sobre a capacidade semanal"
                  fillClassName={scheduledHours > weekly ? "bg-danger-solid" : "bg-accent"} />
              )}
            </div>
            <Stat label="OS vencidas" value={kpis.data.overdue_count} tone={kpis.data.overdue_count > 0 ? "danger" : "neutral"}
              hint={<Link to="/maintenance/work-orders?quick=overdue&view=list" className="text-ok underline underline-offset-2">Ver lista</Link>} />
          </>
        )}
      </section>

      {frozen && (
        <p className="rounded-xl border border-info-solid/30 bg-info-solid/10 px-4 py-3 text-caption text-fg">
          Semana congelada desde segunda 00:00: o que for programado agora para esta semana entra como <strong>encaixe</strong> e não conta no cumprimento da programação.
        </p>
      )}
      <p aria-live="polite" className={clsx("rounded-xl px-4 py-3 text-body font-medium", notice?.tone === "danger" ? "bg-danger-solid/10 text-danger" : "bg-ok-solid/15 text-ok", !notice && "sr-only")}>{notice?.text}</p>

      <div className="grid gap-4 xl:grid-cols-[16rem_1fr]">
        <Card padded={false} className="p-3">
          <CardHeader className="mb-2" title="A programar" description={`${toPlan.length} OS · ${hoursLabel(toPlan.reduce((s, w) => s + remainingHours(w), 0))} restantes`} />
          {wos.isPending ? <LoadingRegion><Skeleton className="h-40" /></LoadingRegion> : wos.isError ? (
            <ErrorState error={wos.error} onRetry={() => void wos.refetch()} />
          ) : toPlan.length === 0 ? <EmptyState className="py-6" title="Nada aprovado aguardando programação" /> : (
            <ul className="space-y-2" aria-label="OS aprovadas a programar">
              {toPlan.map((w) => (
                <li key={w.id}><MiniCard wo={w} today={today} onOpen={() => set({ wo: w.id }, false)} onPlan={() => { setPlanError(null); setPlanning({ wo: w }); }} /></li>
              ))}
            </ul>
          )}
          {pending.length > 0 && (
            <p className="mt-3 border-t border-line pt-3 text-caption text-fg-subtle">
              {pending.length} rascunho(s)/solicitação(ões) ainda não aprovado(s) — fora do backlog.{" "}
              <Link to="/maintenance/work-orders" className="text-ok underline underline-offset-2">Revisar</Link>
            </p>
          )}
        </Card>

        <Card padded={false} className="min-w-0 p-3">
          <div className="-mx-3 overflow-x-auto px-3">
            <table className="w-full min-w-[50rem] table-fixed border-separate border-spacing-1" aria-label="Quadro da semana por técnico e dia">
              <thead>
                <tr>
                  <th scope="col" className="w-24 text-left text-overline uppercase text-fg-subtle">Técnico</th>
                  {days.map((d, i) => {
                    const h = weekWos.filter((w) => dayOf(w.scheduled_for) === d).reduce((s, w) => s + remainingHours(w), 0);
                    const cap = i < 5 ? daily : 0;
                    const win = windows.filter((c) => c.date === d);
                    return (
                      <th key={d} scope="col" className={clsx("align-top text-left font-normal", d === today && "text-ok")}>
                        <span className="block text-caption font-semibold uppercase">{dayLabel(d, i)}</span>
                        <span className={clsx("block text-caption tabular", cap != null && h > cap ? "text-danger" : "text-fg-subtle")}>
                          {formatDec1(h)} / {cap == null ? "—" : formatDec1(cap)} h
                        </span>
                        {win.map((c) => (
                          <span key={c.id} className="mt-0.5 block truncate rounded bg-warn-solid/15 px-1 text-caption text-warn" title={c.note ?? "Janela de parada planejada"}>
                            Janela PLN-03{c.start_local ? ` ${c.start_local}–${c.end_local ?? ""}` : ""}
                          </span>
                        ))}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <th scope="row" className="truncate align-top py-2 text-left text-caption font-medium text-fg-muted">{r.name}</th>
                    {days.map((d) => {
                      const key = `${r.id}|${d}`;
                      return (
                        <td key={d} data-testid="schedule-cell" data-day={d} data-row={r.id}
                          onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; if (over !== key) setOver(key); }}
                          onDragLeave={() => setOver((o) => (o === key ? null : o))}
                          onDrop={(e) => onDrop(e, d, r.id)}
                          className={clsx("h-24 rounded-lg border border-dashed p-1 align-top transition-colors",
                            over === key ? "border-accent bg-accent/10" : d < today ? "border-line bg-surface-2/80" : "border-line bg-surface-2/30")}>
                          <ul className="space-y-1">
                            {cell(d, r.id).map((w) => (
                              <li key={w.id}><MiniCard wo={w} today={today} draggable={w.state === "scheduled"} onOpen={() => set({ wo: w.id }, false)} /></li>
                            ))}
                          </ul>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {techs.isError && <p className="mt-2 text-caption text-fg-subtle">Lista de técnicos indisponível: o quadro mostra a equipe toda numa linha e mantém o responsável de cada OS.</p>}
        </Card>
      </div>

      <Sheet open={!!planning} onClose={() => setPlanning(null)} title={planning ? `Programar ${planning.wo.number}` : "Programar"} description={planning?.wo.title}>
        {planning && (
          <div className="space-y-3">
            <WoTypeBadge type={planning.wo.type} urgency={planning.wo.urgency} />
            <TransitionForm wo={planning.wo} to="scheduled" busy={transition.isPending} error={planError} defaultDate={planning.date ?? (monday < today ? today : monday)}
              onSubmit={(b) => void schedule(planning.wo, b)} onCancel={() => setPlanning(null)} />
          </div>
        )}
      </Sheet>
      <WorkOrderSheet id={f.wo || null} onClose={() => set({ wo: null }, false)} />
    </div>
  );
}
