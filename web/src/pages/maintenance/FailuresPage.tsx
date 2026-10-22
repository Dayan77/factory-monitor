import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Card } from "@/components/ui/Card";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Sheet from "@/components/ui/Sheet";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import FailureForm, { useIsoCodes } from "@/components/maintenance/FailureForm";
import { useFailure, useFailures, useMachineIndex, usePatchFailure, usePositionsIndex } from "@/hooks/useMaintenance";
import { useAuthStore } from "@/store/auth";
import type { FailureRecord } from "@/services/maintenanceTypes";
import { apiErrorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";
import { formatDuration } from "@/utils/efficiency";
import { analysisPending, can, codeLabel, SEVERITY_SHORT } from "@/utils/maintenance";
import { AssetFilters, Pills, useUrlFilters } from "./filters";

const QUICK = [
  { value: "", label: "Todas" },
  { value: "open", label: "Sem restabelecimento" },
  { value: "analysis", label: "Análise pendente (6.4 / 5.5)" },
] as const;
type Quick = (typeof QUICK)[number]["value"];

function severityTone(s: string) {
  return s === "critical" ? "danger" : s === "degraded" ? "warn" : "info";
}

function EditSheet({ failure, onClose, machineName, positionName }: { failure: FailureRecord | null; onClose: () => void; machineName: string; positionName?: string }) {
  const patch = usePatchFailure();
  const user = useAuthStore((s) => s.user);
  const canEdit = can(user, "maintenance.failures");
  const canRelease = can(user, "maintenance.close") && failure && !failure.restored_at && failure.severity !== "incipient";
  return (
    <Sheet open={!!failure} onClose={onClose} size="lg" title="Registro de falha (ISO 14224)"
      description={failure ? `${machineName}${positionName ? ` · ${positionName}` : ""} · ${formatDateTime(failure.failed_at)}` : undefined}>
      {failure && (
        <div className="space-y-5">
          <dl className="grid grid-cols-2 gap-3 text-body">
            <div><dt className="text-caption text-fg-subtle">Falhou em</dt><dd className="font-medium text-fg">{formatDateTime(failure.failed_at)}</dd></div>
            <div>
              <dt className="text-caption text-fg-subtle">Restabelecida em</dt>
              <dd className="font-medium text-fg">{failure.restored_at ? formatDateTime(failure.restored_at) : "— (medido pela máquina ao voltar a produzir)"}</dd>
            </div>
            {failure.work_order_id && <div><dt className="text-caption text-fg-subtle">OS</dt><dd><Link className="text-ok underline underline-offset-2" to={`/maintenance/work-orders?wo=${failure.work_order_id}`}>Abrir OS</Link></dd></div>}
          </dl>
          {canRelease && (
            <div className="rounded-xl border border-line p-3">
              <p className="text-caption text-fg-muted">Máquina sem sinal de funcionamento? Confirme a liberação: vira o restabelecimento da falha (entra no MTTR).</p>
              <Button className="mt-2" variant="secondary" loading={patch.isPending}
                onClick={() => patch.mutate({ id: failure.id, body: { restored_at: new Date().toISOString() } }, { onSuccess: onClose })}>
                Confirmar máquina liberada e produzindo
              </Button>
            </div>
          )}
          {canEdit ? (
            <FailureForm failure={failure} busy={patch.isPending}
              error={patch.isError ? apiErrorMessage(patch.error, "Não foi possível salvar a falha.") : null}
              onSave={(body) => patch.mutate({ id: failure.id, body }, { onSuccess: onClose })} />
          ) : <p className="text-caption text-fg-subtle">Somente leitura para o seu perfil.</p>}
        </div>
      )}
    </Sheet>
  );
}

/** Failures (ISO 14224) — planner completes mode/mechanism/cause; supervisor confirms release (workflows §2 16:30, §4). */
export default function FailuresPage() {
  const { values: f, set } = useUrlFilters(["line", "machine", "quick", "f"] as const);
  const { tree, index } = useMachineIndex();
  const failures = useFailures({ machine_id: f.machine || undefined, open: f.quick === "open" ? true : undefined });
  const { codes } = useIsoCodes();
  const positions = usePositionsIndex(useMemo(() => [...new Set((failures.data ?? []).map((x) => x.machine_id))], [failures.data]));
  const quick = f.quick as Quick;

  const list = useMemo(() => {
    let l = failures.data ?? [];
    if (f.line) l = l.filter((x) => index.get(x.machine_id)?.line_id === f.line);
    if (quick === "open") l = l.filter((x) => !x.restored_at);
    if (quick === "analysis") l = l.filter(analysisPending);
    return l.slice().sort((a, b) => b.failed_at.localeCompare(a.failed_at));
  }, [failures.data, f.line, quick, index]);
  const inList = (failures.data ?? []).find((x) => x.id === f.f) ?? null;
  const single = useFailure(f.f && failures.isSuccess && !inList ? f.f : null);
  const selected = inList ?? single.data ?? null;
  const mName = (x: FailureRecord) => x.machine_name ?? index.get(x.machine_id)?.name ?? "—";
  const pName = (x: FailureRecord) => x.position_name ?? (x.position_id ? positions.get(x.position_id)?.name : undefined);

  return (
    <div className="mx-auto max-w-7xl space-y-5 animate-rise-in">
      <div>
        <h2 className="text-title text-fg">Falhas</h2>
        <p className="text-caption text-fg-subtle">Perda da função requerida (ISO 14224). Complete modo, mecanismo e causa após a análise.</p>
      </div>
      <Card padded={false} className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-3">
          {tree.data && <AssetFilters tree={tree.data} plant="" line={f.line} machine={f.machine} onChange={(p) => set({ line: p.line, machine: p.machine })} />}
        </div>
        <Pills label="Filtro rápido" items={QUICK} value={quick} onChange={(v) => set({ quick: v || null })} />
      </Card>
      <Card padded={false} className="p-2">
        <QueryView query={failures} errorTitle="Não foi possível carregar as falhas"
          loading={<LoadingRegion label="Carregando falhas…"><div className="space-y-2 p-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-12" />)}</div></LoadingRegion>}
          isEmpty={() => list.length === 0} empty={<EmptyState title="Nenhuma falha registrada" description="Falhas entram aqui pela parada EQP-*, pelo chamado de manutenção ou pela inspeção." />}>
          {() => (
            <Table caption="Registros de falha">
              <THead><tr><TH>Item</TH><TH>Falhou em</TH><TH>Duração</TH><TH>Severidade</TH><TH>Modo</TH><TH>Mecanismo · causa</TH><TH>Detecção</TH></tr></THead>
              <TBody>
                {list.map((x) => {
                  const dur = x.restored_at ? (Date.parse(x.restored_at) - Date.parse(x.failed_at)) / 1000 : null;
                  return (
                    <TR key={x.id} interactive tabIndex={0} data-testid="failure-row" onClick={() => set({ f: x.id }, false)}
                      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); set({ f: x.id }, false); } }}>
                      <TD><p className="font-medium text-fg">{mName(x)}</p><p className="text-caption text-fg-subtle">{pName(x) ?? "posição não informada"}</p></TD>
                      <TD className="text-caption">{formatDateTime(x.failed_at)}</TD>
                      <TD className="text-caption">{dur == null ? <Badge tone={x.severity === "incipient" ? "neutral" : "warn"}>{x.severity === "incipient" ? "sem parada" : "em aberto"}</Badge> : formatDuration(dur)}</TD>
                      <TD><Badge tone={severityTone(x.severity)}>{SEVERITY_SHORT[x.severity] ?? x.severity}</Badge></TD>
                      <TD className="text-caption">{codeLabel(codes.modes, x.failure_mode)}</TD>
                      <TD className="text-caption">
                        <span className="block">{codeLabel(codes.mechanisms, x.failure_mechanism)}</span>
                        <span className="block text-fg-subtle">{codeLabel(codes.causes, x.failure_cause)}</span>
                        {analysisPending(x) && <Badge tone="warn" className="mt-1">Análise pendente</Badge>}
                      </TD>
                      <TD className="text-caption text-fg-muted">{codeLabel(codes.detection_methods, x.detection_method).replace(/^[a-z_]+ — /, "")}</TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
        </QueryView>
      </Card>
      <EditSheet failure={selected} onClose={() => set({ f: null }, false)} machineName={selected ? mName(selected) : ""} positionName={selected ? pName(selected) : undefined} />
    </div>
  );
}
