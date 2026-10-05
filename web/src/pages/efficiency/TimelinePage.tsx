import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import clsx from "clsx";
import { Card, CardHeader } from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import QueryView from "@/components/ui/QueryView";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { inputClass } from "@/components/ui/Field";
import Sheet from "@/components/ui/Sheet";
import Badge from "@/components/ui/Badge";
import PeriodPicker from "@/components/efficiency/PeriodPicker";
import StateTimeline from "@/components/efficiency/StateTimeline";
import { useDowntime, usePlant, useReasonCodes, useShifts } from "@/hooks/useEfficiency";
import { useNow } from "@/hooks/useNow";
import type { DowntimeEvent } from "@/services/types";
import { formatDateTime } from "@/utils/format";
import { eventState, formatDuration, reasonLabel, TIMELINE_LABELS } from "@/utils/efficiency";
import { usePeriodParams } from "./usePeriodParams";

const SOURCE_LABEL: Record<string, string> = {
  auto_fault: "Automática — sinal de falha",
  auto_run_state: "Automática — máquina parada",
  auto_cycle_gap: "Automática — sem ciclos",
  manual: "Manual",
};

/** Machine state timeline (Gantt-like) per line for the selected period. */
export default function TimelinePage() {
  const { tree, plant, tz } = usePlant();
  const shifts = useShifts(plant?.id);
  const pp = usePeriodParams(tz, shifts.data);
  const [params] = useSearchParams();
  const lineId = params.get("line") ?? "";
  const reasons = useReasonCodes();
  const now = useNow(30_000);
  const [selected, setSelected] = useState<DowntimeEvent | null>(null);

  const win = pp.period.unavailable ? null : { start: pp.period.start, stop: pp.period.stop };
  const downtime = useDowntime(win ? { ...(lineId ? { line_id: lineId } : {}), ...win, limit: 5000 } : null, 30_000);
  const lines = useMemo(() => (plant?.lines ?? []).filter((l) => !lineId || l.id === lineId), [plant, lineId]);

  if (tree.isError) return <ErrorState error={tree.error} onRetry={() => void tree.refetch()} />;

  return (
    <div className="mx-auto max-w-7xl space-y-6 animate-rise-in">
      <div className="space-y-3">
        <div>
          <h2 className="text-title text-fg">Linha do tempo das máquinas</h2>
          <p className="text-caption text-fg-subtle">{pp.period.label} · toque numa parada para ver o detalhe</p>
        </div>
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-surface p-2 shadow-card">
          <select aria-label="Linha" value={lineId} onChange={(e) => pp.update({ line: e.target.value || null })}
            className={clsx(inputClass, "min-h-9 py-1 sm:!w-auto")}>
            <option value="">Todas as linhas</option>
            {(plant?.lines ?? []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
          <PeriodPicker id="tl-period" value={pp.preset} onChange={pp.setPreset} from={pp.from} to={pp.to} onCustom={pp.setCustom}
            presets={["shift", "today", "yesterday", "7d", "custom"]} />
        </div>
      </div>

      {pp.period.unavailable ? (
        <Card><EmptyState title={pp.period.label} description={pp.period.unavailable} /></Card>
      ) : tree.isPending ? (
        <LoadingRegion><Skeleton className="h-64 rounded-xl" /></LoadingRegion>
      ) : lines.length === 0 ? (
        <Card><EmptyState title="Nenhuma linha" /></Card>
      ) : (
        <QueryView query={downtime} errorTitle="Não foi possível carregar as paradas"
          loading={<LoadingRegion><Skeleton className="h-64 rounded-xl" /></LoadingRegion>}>
          {(evs) => (
            <div className="space-y-4">
              {lines.map((l) => (
                <Card key={l.id}>
                  <CardHeader as="h3" title={l.name} description={`${l.machines.length} máquina(s)`} />
                  {l.machines.length === 0 ? <EmptyState className="py-4" title="Sem máquinas" /> : (
                    <StateTimeline tz={tz} start={Date.parse(pp.period.start)} stop={Date.parse(pp.period.stop)} now={now}
                      reasons={reasons.data} onSelect={setSelected}
                      rows={l.machines.map((m) => ({ id: m.id, name: m.name, events: evs.filter((e) => e.machine_id === m.id) }))} />
                  )}
                </Card>
              ))}
            </div>
          )}
        </QueryView>
      )}

      <Sheet open={!!selected} onClose={() => setSelected(null)} title="Parada"
        description={selected ? `${selected.machine_name ?? ""} · ${formatDateTime(selected.start)}` : undefined}>
        {selected && (
          <dl className="space-y-3 text-body">
            <div className="flex items-center gap-2">
              <Badge tone={eventState(selected) === "error" ? "danger" : eventState(selected) === "planned" ? "info" : "warn"} dot>
                {TIMELINE_LABELS[eventState(selected)]}
              </Badge>
              {selected.end == null && <Badge tone="danger" pulse dot>Em andamento</Badge>}
            </div>
            <div><dt className="text-caption text-fg-subtle">Motivo</dt><dd className="text-fg">{reasonLabel(selected.reason_code, reasons.data)}</dd></div>
            {selected.suggested_reason_code && (
              <div><dt className="text-caption text-fg-subtle">Sugestão automática</dt><dd className="text-fg">{reasonLabel(selected.suggested_reason_code, reasons.data)}</dd></div>
            )}
            <div><dt className="text-caption text-fg-subtle">Duração</dt>
              <dd className="tabular text-fg">{formatDuration(selected.duration_s ?? (Date.now() - Date.parse(selected.start)) / 1000)}</dd></div>
            <div><dt className="text-caption text-fg-subtle">Início → fim</dt><dd className="tabular text-fg">{formatDateTime(selected.start)} → {selected.end ? formatDateTime(selected.end) : "agora"}</dd></div>
            <div><dt className="text-caption text-fg-subtle">Origem</dt><dd className="text-fg">{SOURCE_LABEL[selected.source] ?? selected.source}{selected.fault_code != null && ` · código de falha ${selected.fault_code}`}</dd></div>
            {selected.comment && <div><dt className="text-caption text-fg-subtle">Comentário</dt><dd className="text-fg">{selected.comment}</dd></div>}
          </dl>
        )}
      </Sheet>
    </div>
  );
}
