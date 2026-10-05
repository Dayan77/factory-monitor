import { Link } from "react-router-dom";
import { usePlantTree } from "@/hooks/useAssets";
import type { StatusSummary, TreePlant } from "@/services/types";
import { Card } from "@/components/ui/Card";
import Stat from "@/components/ui/Stat";
import Badge from "@/components/ui/Badge";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import Breadcrumbs from "@/components/ui/Breadcrumbs";
import StatusSummaryBar from "./StatusSummaryBar";
import { lineSeverity } from "./lookup";

function totals(tree: TreePlant[]): Required<StatusSummary> & { machines: number; lines: number } {
  const t = { running: 0, stopped: 0, error: 0, unknown: 0, maintenance: 0, machines: 0, lines: 0 };
  for (const p of tree) {
    for (const l of p.lines) {
      t.lines += 1;
      t.machines += l.machines.length;
      t.running += l.status_summary.running;
      t.stopped += l.status_summary.stopped;
      t.error += l.status_summary.error;
      t.unknown += l.status_summary.unknown;
      t.maintenance += l.status_summary.maintenance ?? 0;
    }
  }
  return t;
}

function Loading() {
  return (
    <LoadingRegion label="Carregando a fábrica…">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-24 rounded-xl" />)}
      </div>
      <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-36 rounded-xl" />)}
      </div>
    </LoadingRegion>
  );
}

export default function FactoryPage() {
  const query = usePlantTree();

  return (
    <div className="mx-auto max-w-7xl space-y-6 animate-rise-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <Breadcrumbs items={[{ label: "Fábrica" }]} />
          <h2 className="mt-1 text-title text-fg">Visão da fábrica</h2>
          <p className="text-body text-fg-subtle">Planta → linha → máquina. Status ao vivo.</p>
        </div>
      </div>

      <QueryView
        query={query}
        loading={<Loading />}
        errorTitle="Não foi possível carregar a fábrica"
        isEmpty={(d) => d.length === 0}
        empty={
          <Card>
            <EmptyState
              title="Nenhuma planta cadastrada"
              description="Cadastre uma planta com suas linhas e máquinas (API /api/plants) para ver a hierarquia aqui."
            />
          </Card>
        }
      >
        {(tree) => {
          const t = totals(tree);
          return (
            <>
              <section aria-label="Resumo" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <Stat label="Máquinas operando" value={`${t.running}`} unit={`/ ${t.machines}`} tone={t.machines > 0 && t.running === t.machines ? "ok" : "neutral"} />
                <Stat label="Em falha" value={t.error} tone={t.error > 0 ? "danger" : "neutral"} hint={t.error > 0 ? "Requer atenção" : "Nenhuma falha ativa"} />
                <Stat label="Paradas / manutenção" value={t.stopped + t.maintenance} tone={t.stopped + t.maintenance > 0 ? "warn" : "neutral"} />
                <Stat label="Sem comunicação" value={t.unknown} hint={`${t.lines} ${t.lines === 1 ? "linha" : "linhas"}`} />
              </section>

              {tree.map((plant) => (
                <section key={plant.id} aria-labelledby={`plant-${plant.id}`} className="space-y-3">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <h3 id={`plant-${plant.id}`} className="text-heading text-fg">{plant.name}</h3>
                    <span className="text-caption text-fg-subtle">{plant.timezone}</span>
                  </div>
                  {plant.lines.length === 0 ? (
                    <Card><EmptyState title="Planta sem linhas" description="Nenhuma linha de produção vinculada a esta planta." /></Card>
                  ) : (
                    <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                      {[...plant.lines]
                        .sort((a, b) => lineSeverity(b) - lineSeverity(a) || a.name.localeCompare(b.name, "pt-BR"))
                        .map((line) => (
                          <li key={line.id}>
                            <Link
                              to={`/factory/lines/${line.id}`}
                              data-testid="line-card"
                              className="group block h-full rounded-xl focus-visible:outline-offset-4"
                            >
                              <Card interactive className="h-full">
                                <div className="mb-3 flex items-start justify-between gap-2">
                                  <div className="min-w-0">
                                    <p className="truncate text-heading text-fg">{line.name}</p>
                                    <p className="text-caption text-fg-subtle">
                                      {line.machines.length} {line.machines.length === 1 ? "máquina" : "máquinas"}
                                    </p>
                                  </div>
                                  {line.status_summary.error > 0 ? (
                                    <Badge tone="danger" dot pulse>{line.status_summary.error} em falha</Badge>
                                  ) : line.machines.length > 0 && line.status_summary.running === line.machines.length ? (
                                    <Badge tone="ok" dot>Tudo operando</Badge>
                                  ) : null}
                                </div>
                                <StatusSummaryBar summary={line.status_summary} />
                                <p className="mt-3 flex items-center gap-1 text-caption font-medium text-ok opacity-80 transition-opacity group-hover:opacity-100">
                                  Ver linha
                                  <svg viewBox="0 0 20 20" fill="currentColor" className="size-4 transition-transform group-hover:translate-x-0.5" aria-hidden="true">
                                    <path fillRule="evenodd" d="M3 10a.75.75 0 0 1 .75-.75h10.64l-3.22-3.22a.75.75 0 1 1 1.06-1.06l4.5 4.5a.75.75 0 0 1 0 1.06l-4.5 4.5a.75.75 0 1 1-1.06-1.06l3.22-3.22H3.75A.75.75 0 0 1 3 10z" clipRule="evenodd" />
                                  </svg>
                                </p>
                              </Card>
                            </Link>
                          </li>
                        ))}
                    </ul>
                  )}
                </section>
              ))}
            </>
          );
        }}
      </QueryView>
    </div>
  );
}
