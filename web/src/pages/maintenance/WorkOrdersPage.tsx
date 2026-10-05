import { useMemo, useState } from "react";
import clsx from "clsx";
import { Card } from "@/components/ui/Card";
import Button from "@/components/ui/Button";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import { Tabs, TabPanel } from "@/components/ui/Tabs";
import { Progress } from "@/components/ui/LifeBar";
import { BreakInBadge, OverdueBadge, WoStateBadge, WoTypeBadge } from "@/components/maintenance/badges";
import WorkOrderSheet from "@/components/maintenance/WorkOrderSheet";
import CreateWorkOrderSheet from "@/components/maintenance/CreateWorkOrderSheet";
import { useTechnicians, useWoNames, useWorkOrders } from "@/hooks/useMaintenance";
import { usePlantTree } from "@/hooks/useAssets";
import { useAuthStore } from "@/store/auth";
import type { WoState, WoType, WorkOrder } from "@/services/maintenanceTypes";
import { formatDate } from "@/utils/format";
import {
  can, dayOf, hoursLabel, isBreakIn, isoDay, isWoOverdue, remainingHours, WO_STATE_LABEL, WO_TYPE_LABEL,
} from "@/utils/maintenance";
import { AssetFilters, Pills, selectClass, useUrlFilters } from "./filters";

const COLUMNS: { key: string; title: string; states: WoState[]; hint?: string }[] = [
  { key: "requests", title: "Rascunhos e solicitações", states: ["draft", "open"], hint: "Fora do backlog até aprovar" },
  { key: "approved", title: "Aprovadas", states: ["approved"] },
  { key: "scheduled", title: "Programadas", states: ["scheduled"] },
  { key: "in_progress", title: "Em execução", states: ["in_progress"] },
  { key: "on_hold", title: "Em espera", states: ["on_hold"] },
  { key: "completed", title: "Concluídas (aguardando fechamento)", states: ["completed"] },
];

const VIEWS = [{ value: "board", label: "Quadro" }, { value: "list", label: "Lista" }] as const;
const QUICK = [
  { value: "", label: "Todas" },
  { value: "overdue", label: "Vencidas" },
  { value: "done_today", label: "Concluídas hoje" },
  { value: "closed", label: "Encerradas" },
] as const;
const TYPES: WoType[] = ["preventive", "predictive", "corrective", "improvement"];
const KEYS = ["view", "line", "machine", "type", "assignee", "quick", "wo", "plan", "state", "from", "to"] as const;

function WoCard({ wo, today, onOpen }: { wo: WorkOrder; today: string; onOpen: () => void }) {
  const names = useWoNames();
  const est = wo.estimated_hours ?? 0;
  const assignee = names.assignee(wo);
  return (
    <button type="button" onClick={onOpen} data-testid="wo-card" data-wo={wo.number}
      className={clsx("w-full rounded-xl border bg-surface p-3 text-left shadow-card transition-colors hover:border-line-strong",
        isWoOverdue(wo, today) ? "border-danger-solid/50" : wo.urgency === "emergency" ? "border-danger-solid/40" : "border-line")}>
      <div className="flex flex-wrap items-start justify-between gap-1">
        <span className="whitespace-nowrap text-caption font-semibold text-fg-muted">{wo.number}</span>
        <WoTypeBadge type={wo.type} urgency={wo.urgency} compact />
      </div>
      <p className="mt-1 line-clamp-2 text-body font-medium text-fg">{wo.title}</p>
      <p className="truncate text-caption text-fg-subtle">{names.machine(wo)}{wo.position_name ? ` · ${wo.position_name}` : ""}</p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {isWoOverdue(wo, today) && <OverdueBadge />}
        {wo.state === "scheduled" && isBreakIn(wo) && <BreakInBadge />}
        {wo.scheduled_for && <span className="text-caption text-fg-muted">{formatDate(wo.scheduled_for)}</span>}
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 text-caption">
        <span className="truncate text-fg-muted">{assignee ?? (wo.assignee_id ? "Atribuída" : "Sem responsável")}</span>
        <span className="shrink-0 tabular text-fg-subtle">{hoursLabel(wo.labor_hours)} / {hoursLabel(wo.estimated_hours)}</span>
      </div>
      {est > 0 && <Progress className="mt-1" size="sm" value={wo.labor_hours / est} label={`Horas apontadas de ${wo.number}`} fillClassName={wo.labor_hours > est ? "bg-warn-solid" : "bg-accent"} />}
    </button>
  );
}

function Board({ list, today, onOpen }: { list: WorkOrder[]; today: string; onOpen: (id: string) => void }) {
  return (
    <div className="-mx-4 overflow-x-auto px-4 pb-2 sm:mx-0 sm:px-0">
      <div className="grid min-w-[72rem] grid-cols-6 gap-3">
        {COLUMNS.map((c) => {
          const items = list.filter((w) => c.states.includes(w.state));
          const hours = items.reduce((s, w) => s + remainingHours(w), 0);
          return (
            <section key={c.key} aria-label={c.title} data-testid={`column-${c.key}`} className="flex min-h-40 flex-col rounded-xl border border-line bg-surface-2/40 p-2">
              <header className="mb-2 px-1">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-caption font-semibold uppercase tracking-wide text-fg-muted">{c.title}</h3>
                  <span className="rounded-full bg-surface px-2 text-caption tabular text-fg">{items.length}</span>
                </div>
                <p className="text-caption text-fg-subtle">{c.hint ?? `${hoursLabel(hours)} restantes`}</p>
              </header>
              <ul className="flex-1 space-y-2">
                {items.map((w) => <li key={w.id}><WoCard wo={w} today={today} onOpen={() => onOpen(w.id)} /></li>)}
                {items.length === 0 && <li className="px-1 py-4 text-center text-caption text-fg-subtle">Nenhuma</li>}
              </ul>
            </section>
          );
        })}
      </div>
    </div>
  );
}

type SortKey = "number" | "scheduled_for" | "state";

function List({ list, today, onOpen }: { list: WorkOrder[]; today: string; onOpen: (id: string) => void }) {
  const names = useWoNames();
  const [sort, setSort] = useState<SortKey>("scheduled_for");
  const rows = useMemo(() => list.slice().sort((a, b) => {
    if (sort === "number") return b.number.localeCompare(a.number, "pt-BR", { numeric: true });
    if (sort === "state") return a.state.localeCompare(b.state);
    return (dayOf(a.scheduled_for) ?? "9999").localeCompare(dayOf(b.scheduled_for) ?? "9999");
  }), [list, sort]);
  const sortBtn = (k: SortKey, label: string) => (
    <button type="button" onClick={() => setSort(k)} aria-pressed={sort === k} className={clsx("uppercase", sort === k && "text-fg")}>{label}{sort === k ? " ↓" : ""}</button>
  );
  return (
    <Table caption="Ordens de serviço">
      <THead>
        <tr>
          <TH>{sortBtn("number", "Nº")}</TH>
          <TH>Título</TH>
          <TH>Tipo</TH>
          <TH>{sortBtn("state", "Estado")}</TH>
          <TH>{sortBtn("scheduled_for", "Programada")}</TH>
          <TH>Responsável</TH>
          <TH numeric>Horas</TH>
        </tr>
      </THead>
      <TBody>
        {rows.map((w) => (
          <TR key={w.id} interactive tabIndex={0} data-testid="wo-row" onClick={() => onOpen(w.id)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(w.id); } }}>
            <TD className="font-semibold">{w.number}</TD>
            <TD>
              <p className="font-medium text-fg">{w.title}</p>
              <p className="text-caption text-fg-subtle">{names.machine(w)}{w.position_name ? ` · ${w.position_name}` : ""}</p>
            </TD>
            <TD><WoTypeBadge type={w.type} urgency={w.urgency} /></TD>
            <TD><div className="flex flex-wrap gap-1"><WoStateBadge state={w.state} />{isWoOverdue(w, today) && <OverdueBadge />}</div></TD>
            <TD>{formatDate(w.scheduled_for)}{w.state === "scheduled" && isBreakIn(w) && <span className="ml-1"><BreakInBadge /></span>}</TD>
            <TD className="text-fg-muted">{names.assignee(w) ?? "—"}</TD>
            <TD numeric>{hoursLabel(w.labor_hours)} / {hoursLabel(w.estimated_hours)}</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

/** Planner/supervisor: work orders board (by state) + list + detail with audit trail (workflows §2, §4). */
export default function WorkOrdersPage() {
  const { values: f, set } = useUrlFilters(KEYS);
  const user = useAuthStore((s) => s.user);
  const tree = usePlantTree();
  const techs = useTechnicians();
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState("");
  const view = (f.view || "board") as "board" | "list";
  const today = isoDay();
  const quick = f.quick;
  const query = {
    line_id: f.line || undefined,
    machine_id: f.machine || undefined,
    type: (f.type || undefined) as WoType | undefined,
    assignee_id: f.assignee || undefined,
    plan_id: f.plan || undefined,
    state: f.state || undefined,
    overdue: quick === "overdue" ? true : undefined,
    from: f.from || undefined,
    to: f.to || undefined,
  };
  const wos = useWorkOrders(query);

  const list = useMemo(() => {
    let l = wos.data ?? [];
    if (quick === "overdue") l = l.filter((w) => isWoOverdue(w, today));
    else if (quick === "done_today") l = l.filter((w) => w.state === "completed" && dayOf(w.completed_at) === today);
    else if (quick === "closed") l = l.filter((w) => w.state === "closed" || w.state === "cancelled");
    else if (!f.state && !f.from) l = l.filter((w) => w.state !== "closed" && w.state !== "cancelled");
    return l;
  }, [wos.data, quick, today, f.state, f.from]);

  const filtersActive = !!(f.line || f.machine || f.type || f.assignee || f.plan || f.state || f.from || quick);

  return (
    <div className="mx-auto max-w-[96rem] space-y-5 animate-rise-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-title text-fg">Ordens de serviço</h2>
          <p className="text-caption text-fg-subtle">Acompanhe a execução. Clique numa OS para ver detalhes, ações e a trilha de auditoria.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Tabs id="wo-view" label="Visualização" items={VIEWS} value={view} onChange={(v) => set({ view: v === "board" ? null : v })} />
          {can(user, "maintenance.request") && <Button variant="primary" onClick={() => setCreating(true)}>Nova solicitação</Button>}
        </div>
      </div>

      <Card padded={false} className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-3">
          {tree.data && <AssetFilters tree={tree.data} plant="" line={f.line} machine={f.machine} onChange={(p) => set({ line: p.line, machine: p.machine })} />}
          <select aria-label="Tipo" value={f.type} onChange={(e) => set({ type: e.target.value || null })} className={selectClass}>
            <option value="">Todos os tipos</option>
            {TYPES.map((t) => <option key={t} value={t}>{WO_TYPE_LABEL[t]}</option>)}
          </select>
          <select aria-label="Responsável" value={f.assignee} onChange={(e) => set({ assignee: e.target.value || null })} className={selectClass} disabled={!techs.data?.length}>
            <option value="">Todos os técnicos</option>
            {(techs.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
          {f.state && <Button size="sm" variant="ghost" onClick={() => set({ state: null })}>Estado: {WO_STATE_LABEL[f.state as WoState] ?? f.state} ✕</Button>}
          {(f.from || f.to) && <Button size="sm" variant="ghost" onClick={() => set({ from: null, to: null })}>Período: {formatDate(f.from)} – {formatDate(f.to)} ✕</Button>}
          {f.plan && <Button size="sm" variant="ghost" onClick={() => set({ plan: null })}>Plano filtrado ✕</Button>}
        </div>
        <Pills label="Filtro rápido" items={QUICK} value={quick as (typeof QUICK)[number]["value"]} onChange={(v) => set({ quick: v || null })} />
      </Card>

      <p aria-live="polite" className={clsx("rounded-xl bg-ok-solid/15 px-4 py-3 text-body font-medium text-ok", !notice && "sr-only")}>{notice}</p>

      <TabPanel id="wo-view" activeValue={view}>
        <QueryView query={wos} errorTitle="Não foi possível carregar as ordens de serviço"
          loading={<LoadingRegion label="Carregando OS…"><div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-60 rounded-xl" />)}</div></LoadingRegion>}
          isEmpty={() => list.length === 0 && view === "list"}
          empty={<Card><EmptyState title={filtersActive ? "Nenhuma OS com esses filtros" : "Nenhuma ordem de serviço"} description={filtersActive ? "Ajuste ou limpe os filtros." : "As OS geradas pelos planos e as solicitações aparecem aqui."} /></Card>}>
          {() => view === "board"
            ? <Board list={list} today={today} onOpen={(id) => set({ wo: id }, false)} />
            : <Card padded={false} className="p-2"><List list={list} today={today} onOpen={(id) => set({ wo: id }, false)} /></Card>}
        </QueryView>
      </TabPanel>

      <WorkOrderSheet id={f.wo || null} onClose={() => set({ wo: null }, false)} />
      <CreateWorkOrderSheet open={creating} onClose={() => setCreating(false)} allowEmergency={can(user, "maintenance.plan")}
        machineId={f.machine || undefined}
        onCreated={(wo) => { setNotice(`${wo.number} aberta — aguardando aprovação do planejador.`); set({ wo: wo.id }, false); }} />
    </div>
  );
}
