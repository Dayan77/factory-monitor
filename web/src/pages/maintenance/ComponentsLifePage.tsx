import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Card } from "@/components/ui/Card";
import Badge from "@/components/ui/Badge";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import LifeBar from "@/components/ui/LifeBar";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import type { ComponentLifeRow } from "@/services/maintenanceTypes";
import { useComponentsLife, useMachineIndex } from "@/hooks/useMaintenance";
import { COMPONENT_TYPE_LABEL, componentTypeLabel, LIFE_MEASURE_LABEL, rulReasonLabel } from "@/utils/assets";
import { formatDate, formatInt } from "@/utils/format";
import { AssetFilters, Pills, selectClass, useUrlFilters } from "./filters";

const BANDS = [
  { value: "80", label: "≥ 80 % (atenção)" },
  { value: "100", label: "≥ 100 % (vida atingida)" },
  { value: "all", label: "Todos" },
] as const;
type Band = (typeof BANDS)[number]["value"];

/** Planner: installed components by % of nominal life (kpis.md question 8, S4-12). */
export default function ComponentsLifePage() {
  const { values: f, set } = useUrlFilters(["line", "machine", "type", "band"] as const);
  const { tree } = useMachineIndex();
  const comps = useComponentsLife(f.line ? { line_id: f.line } : {});
  const band = (f.band || "80") as Band;

  const rows = useMemo(() => (comps.data ?? [])
    .filter((c) => {
      if (f.machine && c.machine_id !== f.machine) return false;
      if (f.type && c.component_type !== f.type) return false;
      if (band !== "all" && (c.life_consumed_pct ?? -1) < Number(band) / 100) return false;
      return true;
    })
    .sort((a, b) => (b.life_consumed_pct ?? -1) - (a.life_consumed_pct ?? -1)), [comps.data, f.machine, f.type, band]);

  const types = useMemo(() => [...new Set((comps.data ?? []).map((c) => c.component_type).filter(Boolean))], [comps.data]);

  return (
    <div className="mx-auto max-w-7xl space-y-5 animate-rise-in">
      <div>
        <h2 className="text-title text-fg">Componentes por % de vida</h2>
        <p className="text-caption text-fg-subtle">Componentes instalados, mais desgastados primeiro. Vida nominal atingida não gera OS sozinha: só se houver plano — você decide.</p>
      </div>
      <Card padded={false} className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-3">
          {tree.data && <AssetFilters tree={tree.data} plant="" line={f.line} machine={f.machine} onChange={(p) => set({ line: p.line, machine: p.machine })} />}
          <select aria-label="Tipo de componente" value={f.type} onChange={(e) => set({ type: e.target.value || null })} className={selectClass}>
            <option value="">Todos os tipos</option>
            {types.map((t) => <option key={t} value={t}>{COMPONENT_TYPE_LABEL[t as keyof typeof COMPONENT_TYPE_LABEL] ?? t}</option>)}
          </select>
        </div>
        <Pills label="Faixa de vida" items={BANDS} value={band} onChange={(v) => set({ band: v === "80" ? null : v })} />
      </Card>
      <Card padded={false} className="p-2">
        <QueryView query={comps} errorTitle="Não foi possível carregar os componentes"
          loading={<LoadingRegion label="Carregando componentes…"><div className="space-y-2 p-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-12" />)}</div></LoadingRegion>}
          isEmpty={() => rows.length === 0}
          empty={<EmptyState title={band === "all" ? "Nenhum componente instalado" : "Nenhum componente nesta faixa"} description={band === "all" ? "Cadastre e instale componentes nas posições das máquinas." : "Ótimo: nada perto do fim da vida nominal."} />}>
          {() => (
            <Table caption="Componentes instalados por vida consumida">
              <THead>
                <tr><TH>Componente</TH><TH>Posição · máquina</TH><TH className="w-56">Vida consumida</TH><TH>Fim projetado</TH><TH>Alerta</TH></tr>
              </THead>
              <TBody>{rows.map((c) => <Row key={c.component_id} c={c} />)}</TBody>
            </Table>
          )}
        </QueryView>
      </Card>
    </div>
  );
}

function Row({ c }: { c: ComponentLifeRow }) {
  return (
    <TR data-testid="component-row">
      <TD>
        <p className="font-medium text-fg">{[c.manufacturer, c.model].filter(Boolean).join(" ") || componentTypeLabel(c.component_type)}</p>
        <p className="text-caption text-fg-subtle">{componentTypeLabel(c.component_type)}{c.serial_number ? ` · série ${c.serial_number}` : ""} · desde {formatDate(c.installed_at)}</p>
      </TD>
      <TD>
        <Link to={`/factory/machines/${c.machine_id}?pos=${c.position_id}`} className="text-ok hover:underline">{c.position_name} · {c.machine_name}</Link>
      </TD>
      <TD>
        <LifeBar fraction={c.life_consumed_pct} measure={c.life_measure} label={`Vida consumida — ${c.position_name}`} />
        {c.life_measure && <p className="mt-0.5 text-caption text-fg-subtle">por {LIFE_MEASURE_LABEL[c.life_measure]}</p>}
      </TD>
      <TD className="text-caption">
        {c.projected_end_date ? (
          <span className="text-fg">{formatDate(c.projected_end_date)}{c.rul_days != null && <span className="block text-fg-subtle">em {formatInt(c.rul_days)} dias{c.rul_measure && c.rul_measure !== c.life_measure ? ` (por ${LIFE_MEASURE_LABEL[c.rul_measure as keyof typeof LIFE_MEASURE_LABEL] ?? c.rul_measure})` : ""}</span>}</span>
        ) : <span className="text-fg-subtle">— {rulReasonLabel(c.rul_reason ?? c.life_reason)}</span>}
        {c.low_confidence && <Badge tone="warn" className="mt-1">Baixa confiança</Badge>}
      </TD>
      <TD>
        {c.alert === "nominal_reached" ? <Badge tone="danger" dot>Vida nominal atingida</Badge>
          : c.alert === "attention" ? <Badge tone="warn" dot>Atenção</Badge>
          : <span className="text-caption text-fg-subtle">—</span>}
      </TD>
    </TR>
  );
}
