import { useState, type FormEvent } from "react";
import clsx from "clsx";
import {
  useCreateComponent, useInstallComponent, usePosition, usePositionComponents, useRemoveComponent, useStockComponents,
} from "@/hooks/useAssets";
import { useAuthStore } from "@/store/auth";
import type { Component, LifeMeasure, PositionDetail, RemovalReason } from "@/services/types";
import Sheet from "@/components/ui/Sheet";
import Button from "@/components/ui/Button";
import Badge from "@/components/ui/Badge";
import LifeBar from "@/components/ui/LifeBar";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import { Field, inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import {
  canManageAssets, componentName, componentTypeLabel, LIFE_MEASURE_LABEL, REMOVAL_REASON_LABEL, rulReasonLabel,
} from "@/utils/assets";
import { AssetMaintenance } from "@/components/maintenance/MaintenancePanels";
import { ConditionPanel } from "@/components/predictive/parts";
import { Tabs, TabPanel } from "@/components/ui/Tabs";
import { can } from "@/utils/maintenance";
import { formatDate, formatDateTime, formatDec1, formatInt, formatPct } from "@/utils/format";
import { apiErrorMessage } from "@/utils/errors";

type Mode = "view" | "install" | "remove" | "create";

const MEASURES: LifeMeasure[] = ["cycles", "hours", "starts"];
const MEASURE_NAME: Record<LifeMeasure, string> = { cycles: "Ciclos", hours: "Horas", starts: "Partidas" };

function fmtMeasure(m: LifeMeasure, v: number | null | undefined) {
  return m === "hours" ? `${formatDec1(v)} h` : formatInt(v);
}

const MEASURE_UNIT: Record<LifeMeasure, string> = { cycles: "ciclos", hours: "h", starts: "partidas" };

/** Age of a component in this position (frozen at removal): "340.000 ciclos · 1.250,0 h"; zero shows as "0 ciclos · 0,0 h". */
export function componentAgeLabel(c: Pick<Component, "usage_since_install">): string {
  const u = c.usage_since_install;
  const shown = MEASURES.filter((m) => (u?.[m] ?? 0) > 0);
  const list = shown.length ? shown : (["cycles", "hours"] as LifeMeasure[]);
  return list.map((m) => (m === "hours" ? `${formatDec1(u?.[m] ?? 0)} h` : `${formatInt(u?.[m] ?? 0)} ${MEASURE_UNIT[m]}`)).join(" · ");
}

function Section({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section className="space-y-3 border-b border-line py-4 first:pt-0 last:border-b-0">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-overline uppercase text-fg-subtle">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-body">
      <dt className="text-fg-muted">{k}</dt>
      <dd className="text-right font-medium tabular text-fg">{v}</dd>
    </div>
  );
}

function LifeSection({ d }: { d: PositionDetail }) {
  const c = d.installed_component;
  const cu = d.component_usage;
  if (!c) return null;
  const nominal = c.nominal_life ?? {};
  const hasNominal = MEASURES.some((m) => nominal[m]);
  return (
    <Section title="Vida útil">
      <LifeBar fraction={cu?.life_consumed_pct ?? null} measure={cu?.life_measure} label="Vida consumida do componente instalado" />
      {cu?.life_consumed_pct != null && cu.life_measure && (
        <p className="text-caption text-fg-subtle">
          Determinada por {LIFE_MEASURE_LABEL[cu.life_measure]} (maior consumo entre as medidas com vida nominal).
        </p>
      )}
      <dl className="space-y-1.5">
        <KV
          k="Fim de vida projetado"
          v={
            cu?.projected_end_date ? (
              <span className="inline-flex items-center gap-2">
                {formatDate(cu.projected_end_date)}
                {cu.overdue && <Badge tone="danger">Vencido</Badge>}
                {cu.low_confidence && <Badge tone="warn" title="Menos de 14 dias de histórico">Baixa confiança</Badge>}
              </span>
            ) : (
              <span className="text-fg-subtle">— {rulReasonLabel(cu?.rul_reason ?? (hasNominal ? null : "no_nominal_life"))}</span>
            )
          }
        />
        {cu?.rul_days != null && <KV k="Vida remanescente" v={`${formatInt(cu.rul_days)} dias`} />}
      </dl>
      {hasNominal ? (
        <table className="w-full text-body">
          <caption className="sr-only">Uso desde a instalação comparado à vida nominal</caption>
          <thead>
            <tr className="text-caption text-fg-subtle">
              <th scope="col" className="py-1 text-left font-medium">Medida</th>
              <th scope="col" className="py-1 text-right font-medium">Usado</th>
              <th scope="col" className="py-1 text-right font-medium">Nominal</th>
              <th scope="col" className="py-1 text-right font-medium">%</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {MEASURES.filter((m) => nominal[m]).map((m) => {
              const used = cu?.[m] ?? 0;
              const nom = nominal[m] as number;
              return (
                <tr key={m}>
                  <th scope="row" className="py-1.5 text-left font-normal text-fg-muted">{MEASURE_NAME[m]}</th>
                  <td className="py-1.5 text-right tabular text-fg">{fmtMeasure(m, used)}</td>
                  <td className="py-1.5 text-right tabular text-fg-muted">{fmtMeasure(m, nom)}</td>
                  <td className="py-1.5 text-right font-semibold tabular text-fg">{formatPct(used / nom)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <p className="text-caption text-fg-subtle">Sem vida nominal cadastrada — informe ciclos, horas ou partidas do datasheet para calcular a vida consumida.</p>
      )}
    </Section>
  );
}

function HistorySection({ positionId }: { positionId: string }) {
  const q = usePositionComponents(positionId);
  return (
    <Section title="Histórico de componentes">
      <QueryView
        query={q}
        loading={<LoadingRegion><Skeleton className="h-12" /></LoadingRegion>}
        errorTitle="Não foi possível carregar o histórico"
        isEmpty={(d) => d.length === 0}
        empty={<p className="text-caption text-fg-subtle">Nenhum componente instalado nesta posição até agora.</p>}
      >
        {(items) => (
          <ol className="relative space-y-3 border-l border-line pl-4" data-testid="component-history">
            {items.map((c) => {
              const installed = c.state === "installed" || (!c.removed_at && c.position_id === positionId);
              return (
                <li key={c.id} className="relative">
                  <span
                    aria-hidden="true"
                    className={clsx("absolute -left-[1.3rem] top-1.5 size-2.5 rounded-full ring-4 ring-surface", installed ? "bg-ok-solid" : "bg-gray-500")}
                  />
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-body font-medium text-fg">{componentName(c)}</p>
                    {installed ? <Badge tone="ok">Instalado</Badge> : c.removal_reason ? <Badge tone={c.removal_reason === "failure" ? "danger" : "neutral"}>{REMOVAL_REASON_LABEL[c.removal_reason]}</Badge> : null}
                  </div>
                  <p className="text-caption text-fg-subtle">
                    {c.serial_number ? `S/N ${c.serial_number} · ` : ""}
                    {formatDate(c.installed_at)} → {installed ? "hoje" : formatDate(c.removed_at)}
                  </p>
                  <p className="text-caption text-fg-muted" data-testid="component-age">
                    {installed ? "Idade" : "Idade na remoção"}: <span className="tabular">{componentAgeLabel(c)}</span>
                    {c.life_consumed_pct != null ? ` · ${formatPct(c.life_consumed_pct)} da vida` : " · sem vida nominal cadastrada"}
                  </p>
                </li>
              );
            })}
          </ol>
        )}
      </QueryView>
    </Section>
  );
}

function InstallForm({ d, onDone }: { d: PositionDetail; onDone: () => void }) {
  const stock = useStockComponents(true);
  const install = useInstallComponent(d.id);
  const [selected, setSelected] = useState("");
  const compatible = (stock.data ?? []).filter((c) => !c.component_type || c.component_type === d.component_type);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!selected) return;
    install.mutate(selected, { onSuccess: onDone });
  }

  return (
    <form onSubmit={submit} className="space-y-3" aria-label="Instalar componente do estoque">
      <QueryView
        query={stock}
        loading={<LoadingRegion><Skeleton className="h-10" /></LoadingRegion>}
        errorTitle="Não foi possível carregar o estoque"
      >
        {() =>
          compatible.length === 0 ? (
            <p className="rounded-lg bg-surface-2 p-3 text-caption text-fg-muted">
              Nenhum {componentTypeLabel(d.component_type).toLowerCase()} em estoque. Cadastre um novo componente.
            </p>
          ) : (
            <Field label="Componente em estoque" htmlFor="install-component">
              <select id="install-component" required value={selected} onChange={(e) => setSelected(e.target.value)} className={inputClass}>
                <option value="">Selecione…</option>
                {compatible.map((c) => (
                  <option key={c.id} value={c.id}>
                    {componentName(c)}{c.serial_number ? ` — S/N ${c.serial_number}` : ""}
                  </option>
                ))}
              </select>
            </Field>
          )
        }
      </QueryView>
      {install.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(install.error, "Não foi possível instalar o componente.")}</p>}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" loading={install.isPending} disabled={!selected}>Instalar</Button>
        <Button variant="ghost" onClick={onDone}>Cancelar</Button>
      </div>
    </form>
  );
}

function RemoveForm({ component, onDone }: { component: Component; onDone: () => void }) {
  const remove = useRemoveComponent();
  const [reason, setReason] = useState<RemovalReason | "">("");

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!reason) return;
    // Per-call onSuccess runs only while this form is mounted. Once the refetched position has
    // no installed component this form unmounts and DrawerBody resets the mode itself; a late
    // onDone (mutateAsync().then) would instead close whatever the user opened meanwhile.
    remove.mutate({ componentId: component.id, reason }, { onSuccess: onDone });
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border border-danger-solid/30 bg-danger-solid/5 p-3" aria-label="Remover componente">
      <p className="text-body text-fg">
        Remover <strong>{componentName(component)}</strong>? O componente sai da posição e mantém o uso acumulado no histórico.
      </p>
      <Field label="Motivo da remoção" htmlFor="remove-reason">
        <select id="remove-reason" required value={reason} onChange={(e) => setReason(e.target.value as RemovalReason)} className={inputClass}>
          <option value="">Selecione…</option>
          {(Object.keys(REMOVAL_REASON_LABEL) as RemovalReason[]).map((r) => (
            <option key={r} value={r}>{REMOVAL_REASON_LABEL[r]}</option>
          ))}
        </select>
      </Field>
      {remove.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(remove.error, "Não foi possível remover o componente.")}</p>}
      <div className="flex gap-2">
        <Button type="submit" variant="danger" loading={remove.isPending} disabled={!reason}>Confirmar remoção</Button>
        <Button variant="ghost" onClick={onDone}>Cancelar</Button>
      </div>
    </form>
  );
}

function parsePositive(v: string): number | null {
  if (!v.trim()) return null;
  const n = Number(v.replace(",", "."));
  return Number.isFinite(n) && n > 0 ? n : NaN;
}

function CreateForm({ d, onDone }: { d: PositionDetail; onDone: () => void }) {
  const create = useCreateComponent();
  const [f, setF] = useState({ manufacturer: "", model: "", serial_number: "", cycles: "", hours: "", starts: "" });
  const [installNow, setInstallNow] = useState(!d.installed_component);
  const [invalid, setInvalid] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF((s) => ({ ...s, [k]: e.target.value }));

  function submit(e: FormEvent) {
    e.preventDefault();
    const cycles = parsePositive(f.cycles);
    const hours = parsePositive(f.hours);
    const starts = parsePositive(f.starts);
    if ([cycles, hours, starts].some((n) => Number.isNaN(n))) {
      setInvalid("A vida nominal deve ser um número maior que zero.");
      return;
    }
    setInvalid(null);
    const nominal_life: Record<string, number> = {};
    if (cycles) nominal_life.cycles = Math.round(cycles);
    if (hours) nominal_life.hours = hours;
    if (starts) nominal_life.starts = Math.round(starts);
    create.mutate(
      {
        component_type: d.component_type,
        manufacturer: f.manufacturer.trim() || null,
        model: f.model.trim() || null,
        serial_number: f.serial_number.trim() || null,
        nominal_life,
        position_id: installNow && !d.installed_component ? d.id : null,
      },
      { onSuccess: onDone },
    );
  }

  return (
    <form onSubmit={submit} className="space-y-3" aria-label="Cadastrar componente">
      <p className="text-caption text-fg-subtle">Tipo: {componentTypeLabel(d.component_type)}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Fabricante" htmlFor="c-manufacturer"><input id="c-manufacturer" className={inputClass} value={f.manufacturer} onChange={set("manufacturer")} /></Field>
        <Field label="Modelo" htmlFor="c-model"><input id="c-model" className={inputClass} value={f.model} onChange={set("model")} /></Field>
      </div>
      <Field label="Número de série" htmlFor="c-serial"><input id="c-serial" className={inputClass} value={f.serial_number} onChange={set("serial_number")} /></Field>
      <fieldset className="space-y-2">
        <legend className="text-caption font-medium text-fg-muted">Vida nominal (datasheet) — preencha as que se aplicam</legend>
        <div className="grid grid-cols-3 gap-2">
          <Field label="Ciclos" htmlFor="c-cycles"><input id="c-cycles" inputMode="numeric" className={inputClass} value={f.cycles} onChange={set("cycles")} placeholder="5000000" /></Field>
          <Field label="Horas" htmlFor="c-hours"><input id="c-hours" inputMode="decimal" className={inputClass} value={f.hours} onChange={set("hours")} placeholder="30000" /></Field>
          <Field label="Partidas" htmlFor="c-starts"><input id="c-starts" inputMode="numeric" className={inputClass} value={f.starts} onChange={set("starts")} /></Field>
        </div>
      </fieldset>
      {!d.installed_component && (
        <label className="flex min-h-10 items-center gap-2 text-body text-fg">
          <input type="checkbox" checked={installNow} onChange={(e) => setInstallNow(e.target.checked)} className="size-4 accent-[rgb(var(--c-accent))]" />
          Instalar nesta posição agora
        </label>
      )}
      {(invalid || create.isError) && (
        <p role="alert" className="text-caption text-danger">{invalid ?? apiErrorMessage(create.error, "Não foi possível cadastrar o componente.")}</p>
      )}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" loading={create.isPending}>Cadastrar</Button>
        <Button variant="ghost" onClick={onDone}>Cancelar</Button>
      </div>
    </form>
  );
}

function DrawerBody({ d }: { d: PositionDetail }) {
  const user = useAuthStore((s) => s.user);
  const role = user?.role;
  const admin = canManageAssets(role);
  const [mode, setMode] = useState<Mode>("view");
  const c = d.installed_component;
  const back = () => setMode("view");
  // Any install/remove changes the installed component; a form tied to the old state
  // (e.g. RemoveForm, rendered only while `c` exists) must not leave the drawer stuck.
  const installedId = c?.id ?? null;
  const [seenInstalledId, setSeenInstalledId] = useState(installedId);
  if (seenInstalledId !== installedId) {
    setSeenInstalledId(installedId);
    setMode("view");
  }

  return (
    <div>
      <Section
        title="Componente instalado"
        action={admin && c && mode === "view" ? <Button size="sm" variant="danger-ghost" onClick={() => setMode("remove")}>Remover</Button> : undefined}
      >
        {c ? (
          <>
            <div>
              <p className="text-heading text-fg">{componentName(c)}</p>
              <p className="text-caption text-fg-subtle">{componentTypeLabel(c.component_type ?? d.component_type)}</p>
            </div>
            <dl className="space-y-1.5">
              <KV k="Número de série" v={c.serial_number ?? "—"} />
              <KV k="Instalado em" v={formatDateTime(c.installed_at)} />
            </dl>
            {mode === "remove" && <RemoveForm component={c} onDone={back} />}
          </>
        ) : (
          <EmptyState className="py-4" title="Nenhum componente instalado" description="A posição continua acumulando uso; instale um componente para acompanhar sua vida." />
        )}
        {admin && mode === "view" && (
          <div className="flex flex-wrap gap-2">
            {!c && <Button variant="primary" onClick={() => setMode("install")}>Instalar do estoque</Button>}
            <Button onClick={() => setMode("create")}>Cadastrar novo componente</Button>
          </div>
        )}
        {mode === "install" && <InstallForm d={d} onDone={back} />}
        {mode === "create" && <CreateForm d={d} onDone={back} />}
      </Section>

      <LifeSection d={d} />

      <Section title="Uso acumulado da posição">
        <dl className="grid grid-cols-3 gap-3">
          {(["cycles", "hours", "starts"] as const).map((m) => (
            <div key={m} className="rounded-lg bg-surface-2/60 p-3">
              <dt className="text-caption text-fg-subtle">{MEASURE_NAME[m]}</dt>
              <dd className="text-heading tabular text-fg">{fmtMeasure(m, d.usage[`${m}_total`])}</dd>
            </div>
          ))}
        </dl>
        <p className="text-caption text-fg-subtle">
          Nunca zera — soma todos os componentes que passaram por esta posição.
          {d.usage.cycles_estimated && ` Ciclos estimados: peças × ${d.cycles_per_part} ciclo(s) por peça.`}
        </p>
      </Section>

      {(can(user, "maintenance.view") || can(user, "maintenance.request")) && (
        <Section title="Manutenção">
          <AssetMaintenance machineId={d.machine_id} positionId={d.id} compact />
        </Section>
      )}

      <HistorySection positionId={d.id} />
    </div>
  );
}

const DRAWER_TABS = [{ value: "general", label: "Geral" }, { value: "condition", label: "Condição" }] as const;

/** "Geral" (component, life, usage, maintenance) and — for roles that read predictive data — "Condição" (P-12). */
function PositionTabs({ d }: { d: PositionDetail }) {
  const user = useAuthStore((s) => s.user);
  const [tab, setTab] = useState<"general" | "condition">("general");
  if (!can(user, "predictive.view")) return <DrawerBody d={d} />;
  return (
    <div className="space-y-4">
      <Tabs id={`pos-${d.id}`} label="Seções da posição" items={DRAWER_TABS} value={tab} onChange={setTab} />
      <TabPanel id={`pos-${d.id}`} activeValue={tab}>
        {tab === "general" ? <DrawerBody d={d} /> : <ConditionPanel positionId={d.id} compact />}
      </TabPanel>
    </div>
  );
}

export default function PositionDrawer({ positionId, onClose }: { positionId: string | null; onClose: () => void }) {
  const q = usePosition(positionId);
  return (
    <Sheet
      open={!!positionId}
      onClose={onClose}
      title={q.data?.name ?? "Posição"}
      description={q.data ? `${componentTypeLabel(q.data.component_type)} · ${q.data.cycles_per_part} ciclo(s) por peça` : undefined}
    >
      <QueryView
        query={q}
        loading={<LoadingRegion label="Carregando posição…"><div className="space-y-3"><Skeleton className="h-6 w-40" /><Skeleton className="h-20" /><Skeleton className="h-32" /></div></LoadingRegion>}
        errorTitle="Não foi possível carregar a posição"
      >
        {(d) => <PositionTabs key={d.id} d={d} />}
      </QueryView>
    </Sheet>
  );
}
