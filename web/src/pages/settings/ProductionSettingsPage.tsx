import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import clsx from "clsx";
import Button from "@/components/ui/Button";
import Badge from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import QueryView from "@/components/ui/QueryView";
import Sheet from "@/components/ui/Sheet";
import { Field, inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import { Tabs, TabPanel } from "@/components/ui/Tabs";
import {
  useCalendarExceptions, useCreateCalendarException, useCreateReasonCode, useDeleteCalendarException, useDeleteProduct,
  useDeleteShift, useMachineProducts, usePlant, useProducts, useReasonCodes, useRemoveMachineProduct, useSaveMachineProduct,
  useSaveProduct, useSaveShift, useShifts, useUpdateReasonCode,
} from "@/hooks/useEfficiency";
import { useAuthStore } from "@/store/auth";
import type { CalendarExceptionKind, MachineProduct, OeeLossKind, Shift, ShiftBreak, TreePlant } from "@/services/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatDate } from "@/utils/format";
import { categoryLabel } from "@/utils/efficiency";

const TABS = [
  { value: "shifts", label: "Turnos" },
  { value: "calendar", label: "Calendário" },
  { value: "products", label: "Produtos e ciclos" },
  { value: "reasons", label: "Motivos de parada" },
] as const;
type TabKey = (typeof TABS)[number]["value"];

const WEEKDAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];
const KIND_LABEL: Record<CalendarExceptionKind, string> = { holiday: "Feriado / sem produção", extra_shift: "Turno extra", planned_stop: "Parada planejada" };
const LOSS_LABEL: Record<string, string> = { none: "Nenhuma", availability: "Disponibilidade", performance: "Desempenho", quality: "Qualidade" };
export const REASON_CODE_RE = /^[A-Z]{3}-\d{2}(-[A-Z0-9]{1,6})?$/;

function FormError({ error, fallback }: { error: unknown; fallback: string }) {
  if (!error) return null;
  return <p role="alert" className="text-caption text-danger">{apiErrorMessage(error, fallback)}</p>;
}

function ReadOnlyNote({ canEdit }: { canEdit: boolean }) {
  if (canEdit) return null;
  return <p className="rounded-lg bg-surface-2 px-3 py-2 text-caption text-fg-muted">Somente administradores alteram estas configurações.</p>;
}

// ── shifts ────────────────────────────────────────────────────────

interface ShiftDraft { name: string; start_local: string; end_local: string; weekdays: number[]; breaks: ShiftBreak[]; is_active: boolean }
const EMPTY_SHIFT: ShiftDraft = { name: "", start_local: "06:00", end_local: "14:00", weekdays: [0, 1, 2, 3, 4], breaks: [], is_active: true };

export function shiftHours(s: Pick<Shift, "start_local" | "end_local" | "breaks">): number {
  const m = (t: string) => { const [h, mm] = t.split(":").map(Number); return h * 60 + mm; };
  const span = (a: string, b: string) => ((m(b) - m(a)) + 1440) % 1440 || 1440;
  const total = span(s.start_local, s.end_local);
  const br = s.breaks.reduce((acc, b) => acc + span(b.start_local, b.end_local), 0);
  return (total - br) / 60;
}

const NET_HOURS = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });

function localToday(): string {
  const t = new Date();
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`;
}

/** Hours, weekdays, breaks or active flag differ from the saved shift (the edit creates a new version). */
export function scheduleChanged(shift: Pick<Shift, "start_local" | "end_local" | "weekdays" | "breaks" | "is_active">, d: ShiftDraft): boolean {
  const br = (bs: ShiftBreak[]) => JSON.stringify(bs.map((b) => [b.start_local, b.end_local, b.reason_code ?? "PLN-01"]));
  return shift.start_local !== d.start_local || shift.end_local !== d.end_local || shift.is_active !== d.is_active
    || [...shift.weekdays].sort().join() !== [...d.weekdays].sort().join() || br(shift.breaks) !== br(d.breaks);
}

function ShiftSheet({ open, shift, plantId, onClose }: { open: boolean; shift: Shift | null; plantId: string; onClose: () => void }) {
  const save = useSaveShift();
  const [d, setD] = useState<ShiftDraft>(EMPTY_SHIFT);
  const [effectiveFrom, setEffectiveFrom] = useState(localToday());
  useEffect(() => {
    if (!open) return;
    save.reset();
    setEffectiveFrom(localToday());
    setD(shift ? { name: shift.name, start_local: shift.start_local, end_local: shift.end_local, weekdays: shift.weekdays, breaks: shift.breaks.map((b) => ({ ...b })), is_active: shift.is_active } : EMPTY_SHIFT);
  }, [open, shift]); // eslint-disable-line react-hooks/exhaustive-deps

  const versioned = !!shift && scheduleChanged(shift, d);
  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate({
      id: shift?.id,
      body: { ...d, name: d.name.trim(), ...(shift ? {} : { plant_id: plantId }), ...(versioned ? { effective_from: effectiveFrom } : {}) },
    }, { onSuccess: onClose });
  }
  const valid = d.name.trim() && d.weekdays.length > 0 && d.start_local !== d.end_local;
  return (
    <Sheet open={open} onClose={onClose} title={shift ? "Editar turno" : "Novo turno"} description="Horários no fuso da planta. Um turno que passa da meia-noite pertence ao dia em que começa."
      footer={<div className="space-y-2"><FormError error={save.error} fallback="Não foi possível salvar o turno." />
        <Button type="submit" form="shift-form" variant="primary" size="lg" className="w-full" disabled={!valid} loading={save.isPending}>Salvar turno</Button></div>}>
      <form id="shift-form" onSubmit={submit} className="space-y-4">
        <Field label="Nome" htmlFor="sh-name"><input id="sh-name" value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} className={inputClass} placeholder="Turno A" required /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Início" htmlFor="sh-start"><input id="sh-start" type="time" value={d.start_local} onChange={(e) => setD({ ...d, start_local: e.target.value })} className={inputClass} required /></Field>
          <Field label="Fim" htmlFor="sh-end"><input id="sh-end" type="time" value={d.end_local} onChange={(e) => setD({ ...d, end_local: e.target.value })} className={inputClass} required /></Field>
        </div>
        <fieldset>
          <legend className="mb-1.5 text-caption font-medium text-fg-muted">Dias da semana</legend>
          <div className="flex flex-wrap gap-1.5">
            {WEEKDAYS.map((w, i) => {
              const on = d.weekdays.includes(i);
              return (
                <button key={w} type="button" aria-pressed={on}
                  onClick={() => setD({ ...d, weekdays: on ? d.weekdays.filter((x) => x !== i) : [...d.weekdays, i].sort() })}
                  className={clsx("min-h-10 min-w-12 rounded-lg border px-2 text-caption font-medium", on ? "border-accent bg-accent/15 text-ok" : "border-line text-fg-muted hover:bg-surface-2")}>
                  {w}
                </button>
              );
            })}
          </div>
        </fieldset>
        <fieldset className="space-y-2">
          <legend className="mb-1.5 text-caption font-medium text-fg-muted">Pausas (refeição, café — tempo planejado)</legend>
          {d.breaks.map((b, i) => (
            <div key={i} className="flex items-end gap-2">
              <Field label="Início" htmlFor={`br-s-${i}`} className="flex-1">
                <input id={`br-s-${i}`} type="time" value={b.start_local} className={inputClass}
                  onChange={(e) => setD({ ...d, breaks: d.breaks.map((x, j) => (j === i ? { ...x, start_local: e.target.value } : x)) })} />
              </Field>
              <Field label="Fim" htmlFor={`br-e-${i}`} className="flex-1">
                <input id={`br-e-${i}`} type="time" value={b.end_local} className={inputClass}
                  onChange={(e) => setD({ ...d, breaks: d.breaks.map((x, j) => (j === i ? { ...x, end_local: e.target.value } : x)) })} />
              </Field>
              <Button variant="danger-ghost" aria-label={`Remover pausa ${i + 1}`} onClick={() => setD({ ...d, breaks: d.breaks.filter((_, j) => j !== i) })}>Remover</Button>
            </div>
          ))}
          <Button size="sm" onClick={() => setD({ ...d, breaks: [...d.breaks, { start_local: "10:00", end_local: "10:15", reason_code: "PLN-01" }] })}>+ Adicionar pausa</Button>
        </fieldset>
        <label className="flex min-h-10 items-center gap-2 text-body text-fg">
          <input type="checkbox" checked={d.is_active} onChange={(e) => setD({ ...d, is_active: e.target.checked })} className="size-4" /> Turno ativo
        </label>
        {versioned && (
          <div role="note" className="space-y-2 rounded-lg border border-warn-solid/40 bg-warn-solid/10 px-3 py-2.5">
            <p className="text-caption text-fg">
              A mudança vale a partir do dia escolhido. Os dias anteriores continuam com o horário antigo — OEE, relatórios de turno
              e paradas já registradas não mudam.
            </p>
            <Field label="Vale a partir de" htmlFor="sh-eff">
              <input id="sh-eff" type="date" value={effectiveFrom} onChange={(e) => setEffectiveFrom(e.target.value)} className={inputClass} required />
            </Field>
          </div>
        )}
      </form>
    </Sheet>
  );
}

function ShiftsTab({ plant, canEdit }: { plant: TreePlant; canEdit: boolean }) {
  const shifts = useShifts(plant.id);
  const del = useDeleteShift();
  const [editing, setEditing] = useState<Shift | null | "new">(null);
  return (
    <Card>
      <CardHeader title="Turnos e pausas" description={`Calendário de produção da planta ${plant.name} (${plant.timezone}). Define o tempo programado (POT) do OEE.`}
        action={canEdit && <Button variant="primary" onClick={() => setEditing("new")}>Novo turno</Button>} />
      <ReadOnlyNote canEdit={canEdit} />
      <QueryView query={shifts} loading={<Skeleton className="h-32" />} errorTitle="Não foi possível carregar os turnos" isEmpty={(d) => d.length === 0}
        empty={<EmptyState title="Nenhum turno cadastrado" description="Sem turnos o OEE considera 24 h programadas por dia (aviso “calendário padrão”)." />}>
        {(list) => (
          <ul className="mt-2 grid gap-3 md:grid-cols-2">
            {list.map((s) => (
              <li key={s.id} className="rounded-xl border border-line p-4" data-testid="shift-item">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-heading text-fg">{s.name} {!s.is_active && <Badge className="ml-1">Inativo</Badge>}</p>
                    <p className="tabular text-body text-fg-muted">{s.start_local}–{s.end_local} · {NET_HOURS.format(shiftHours(s))} h líquidas</p>
                    {s.valid_from && <p className="text-caption text-fg-subtle">Vigente desde {formatDate(s.valid_from)}</p>}
                  </div>
                  {canEdit && (
                    <div className="flex gap-1">
                      <Button size="sm" variant="ghost" onClick={() => setEditing(s)}>Editar</Button>
                      <Button size="sm" variant="danger-ghost" loading={del.isPending && del.variables === s.id}
                        onClick={() => { if (window.confirm(`Excluir o turno ${s.name}? Se ele já rodou, é encerrado a partir de hoje e o histórico continua com o horário dele.`)) del.mutate(s.id); }}>Excluir</Button>
                    </div>
                  )}
                </div>
                <div className="mt-2 flex flex-wrap gap-1">
                  {WEEKDAYS.map((w, i) => <Badge key={w} tone={s.weekdays.includes(i) ? "accent" : "neutral"} className={clsx(!s.weekdays.includes(i) && "opacity-50")}>{w}</Badge>)}
                </div>
                {s.breaks.length > 0 && <p className="mt-2 text-caption text-fg-subtle">Pausas: {s.breaks.map((b) => `${b.start_local}–${b.end_local}`).join(", ")}</p>}
              </li>
            ))}
          </ul>
        )}
      </QueryView>
      <FormError error={del.error} fallback="Não foi possível excluir o turno." />
      <ShiftSheet open={editing !== null} shift={editing === "new" ? null : editing} plantId={plant.id} onClose={() => setEditing(null)} />
    </Card>
  );
}

// ── calendar exceptions ───────────────────────────────────────────

function CalendarTab({ plant, canEdit }: { plant: TreePlant; canEdit: boolean }) {
  const list = useCalendarExceptions(plant.id);
  const shifts = useShifts(plant.id);
  const reasons = useReasonCodes();
  const create = useCreateCalendarException();
  const del = useDeleteCalendarException();
  const [f, setF] = useState({ date: "", kind: "holiday" as CalendarExceptionKind, shift_id: "", start_local: "", end_local: "", reason_code: "", note: "" });
  const planned = (reasons.data ?? []).filter((r) => r.planned && r.is_active);
  const needsTime = f.kind === "extra_shift";
  const valid = !!f.date && (!needsTime || (f.start_local && f.end_local)) && (!f.start_local === !f.end_local);

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate({
      plant_id: plant.id, date: f.date, kind: f.kind, shift_id: f.shift_id || null,
      start_local: f.start_local || null, end_local: f.end_local || null, reason_code: f.reason_code || null, note: f.note.trim() || null,
    }, { onSuccess: () => setF({ ...f, date: "", note: "", start_local: "", end_local: "" }) });
  }
  const shiftName = (id: string | null) => shifts.data?.find((s) => s.id === id)?.name ?? "Todos os turnos";

  return (
    <Card>
      <CardHeader title="Exceções do calendário" description="Feriados, turnos extras e paradas planejadas em datas específicas." />
      <ReadOnlyNote canEdit={canEdit} />
      {canEdit && (
        <form onSubmit={submit} className="mb-5 grid gap-3 rounded-xl border border-line bg-surface-2/40 p-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Data" htmlFor="ce-date"><input id="ce-date" type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} className={inputClass} required /></Field>
          <Field label="Tipo" htmlFor="ce-kind">
            <select id="ce-kind" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as CalendarExceptionKind })} className={inputClass}>
              {Object.entries(KIND_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </Field>
          <Field label="Turno" htmlFor="ce-shift">
            <select id="ce-shift" value={f.shift_id} onChange={(e) => setF({ ...f, shift_id: e.target.value })} className={inputClass}>
              <option value="">Todos / não se aplica</option>
              {(shifts.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>
          <Field label="Motivo planejado" htmlFor="ce-reason">
            <select id="ce-reason" value={f.reason_code} onChange={(e) => setF({ ...f, reason_code: e.target.value })} className={inputClass}>
              <option value="">—</option>
              {planned.map((r) => <option key={r.code} value={r.code}>{r.code} · {r.label}</option>)}
            </select>
          </Field>
          <Field label={needsTime ? "Início" : "Início (opcional)"} htmlFor="ce-start"><input id="ce-start" type="time" value={f.start_local} onChange={(e) => setF({ ...f, start_local: e.target.value })} className={inputClass} /></Field>
          <Field label={needsTime ? "Fim" : "Fim (opcional)"} htmlFor="ce-end"><input id="ce-end" type="time" value={f.end_local} onChange={(e) => setF({ ...f, end_local: e.target.value })} className={inputClass} /></Field>
          <Field label="Observação" htmlFor="ce-note"><input id="ce-note" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} className={inputClass} maxLength={500} /></Field>
          <div className="flex flex-col justify-end gap-1">
            <Button type="submit" variant="primary" disabled={!valid} loading={create.isPending}>Adicionar</Button>
          </div>
          <div className="sm:col-span-2 lg:col-span-4"><FormError error={create.error} fallback="Não foi possível adicionar a exceção." /></div>
        </form>
      )}
      <QueryView query={list} loading={<Skeleton className="h-24" />} errorTitle="Não foi possível carregar o calendário" isEmpty={(d) => d.length === 0}
        empty={<EmptyState className="py-6" title="Nenhuma exceção cadastrada" />}>
        {(rows) => (
          <Table caption="Exceções do calendário">
            <THead><tr><TH>Data</TH><TH>Tipo</TH><TH>Turno</TH><TH>Horário</TH><TH>Motivo / obs.</TH>{canEdit && <TH><span className="sr-only">Ações</span></TH>}</tr></THead>
            <TBody>
              {rows.slice().sort((a, b) => b.date.localeCompare(a.date)).map((r) => (
                <TR key={r.id}>
                  <TD className="tabular">{formatDate(r.date)}</TD>
                  <TD>{KIND_LABEL[r.kind] ?? r.kind}</TD>
                  <TD>{shiftName(r.shift_id)}</TD>
                  <TD className="tabular">{r.start_local ? `${r.start_local}–${r.end_local}` : "Dia todo"}</TD>
                  <TD className="text-fg-muted">{[r.reason_code, r.note].filter(Boolean).join(" · ") || "—"}</TD>
                  {canEdit && <TD className="text-right"><Button size="sm" variant="danger-ghost" onClick={() => del.mutate(r.id)}>Excluir</Button></TD>}
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </QueryView>
    </Card>
  );
}

// ── products & ideal cycle times ──────────────────────────────────

function MachineProductRow({ mp, machineId, canEdit }: { mp: MachineProduct; machineId: string; canEdit: boolean }) {
  const save = useSaveMachineProduct(machineId);
  const remove = useRemoveMachineProduct(machineId);
  const [cycle, setCycle] = useState(String(mp.ideal_cycle_time_s));
  const [ppc, setPpc] = useState(String(mp.parts_per_cycle));
  useEffect(() => { setCycle(String(mp.ideal_cycle_time_s)); setPpc(String(mp.parts_per_cycle)); }, [mp.ideal_cycle_time_s, mp.parts_per_cycle]);
  const c = Number(cycle.replace(",", "."));
  const p = Number(ppc);
  const dirty = c !== mp.ideal_cycle_time_s || p !== mp.parts_per_cycle;
  const valid = c > 0 && Number.isInteger(p) && p >= 1;
  return (
    <TR>
      <TD><span className="font-medium">{mp.product_name ?? "—"}</span> <span className="text-caption text-fg-subtle">{mp.product_code}</span></TD>
      <TD numeric>
        {canEdit ? <input aria-label={`Ciclo ideal de ${mp.product_name}`} inputMode="decimal" value={cycle} onChange={(e) => setCycle(e.target.value)} className={clsx(inputClass, "w-24 text-right")} /> : mp.ideal_cycle_time_s}
      </TD>
      <TD numeric>
        {canEdit ? <input aria-label={`Peças por ciclo de ${mp.product_name}`} inputMode="numeric" value={ppc} onChange={(e) => setPpc(e.target.value)} className={clsx(inputClass, "w-20 text-right")} /> : mp.parts_per_cycle}
      </TD>
      <TD numeric>{mp.ideal_time_per_part_s ? mp.ideal_time_per_part_s.toFixed(2).replace(".", ",") : "—"}</TD>
      <TD>
        {mp.is_default ? <Badge tone="accent">Padrão</Badge> : canEdit ? (
          <Button size="sm" variant="ghost" onClick={() => save.mutate({ mpId: mp.id, body: { product_id: mp.product_id, ideal_cycle_time_s: mp.ideal_cycle_time_s, parts_per_cycle: mp.parts_per_cycle, is_default: true } })}>Tornar padrão</Button>
        ) : null}
      </TD>
      {canEdit && (
        <TD className="whitespace-nowrap text-right">
          {dirty && <Button size="sm" variant="primary" disabled={!valid} loading={save.isPending}
            onClick={() => save.mutate({ mpId: mp.id, body: { product_id: mp.product_id, ideal_cycle_time_s: c, parts_per_cycle: p } })}>Salvar</Button>}
          <Button size="sm" variant="danger-ghost" loading={remove.isPending} onClick={() => remove.mutate(mp.id)}>Remover</Button>
          <FormError error={save.error ?? remove.error} fallback="Erro ao salvar." />
        </TD>
      )}
    </TR>
  );
}

function ProductsTab({ plant, canEdit }: { plant: TreePlant; canEdit: boolean }) {
  const products = useProducts();
  const saveProduct = useSaveProduct();
  const delProduct = useDeleteProduct();
  const [np, setNp] = useState({ code: "", name: "" });
  const machines = useMemo(() => plant.lines.flatMap((l) => l.machines.map((m) => ({ ...m, line: l.name }))), [plant]);
  const [machineId, setMachineId] = useState("");
  const mid = machineId || machines[0]?.id || "";
  const mps = useMachineProducts(mid || null);
  const addMp = useSaveMachineProduct(mid);
  const [nm, setNm] = useState({ product_id: "", cycle: "", ppc: "1", is_default: false });
  const available = (products.data ?? []).filter((p) => !(mps.data ?? []).some((m) => m.product_id === p.id));
  const cycle = Number(nm.cycle.replace(",", "."));

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Produtos" description="Catálogo de produtos da empresa." />
        <ReadOnlyNote canEdit={canEdit} />
        {canEdit && (
          <form className="mb-4 flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); saveProduct.mutate({ body: { code: np.code.trim(), name: np.name.trim() } }, { onSuccess: () => setNp({ code: "", name: "" }) }); }}>
            <Field label="Código" htmlFor="p-code" className="w-36"><input id="p-code" value={np.code} onChange={(e) => setNp({ ...np, code: e.target.value })} className={inputClass} maxLength={64} required /></Field>
            <Field label="Nome" htmlFor="p-name" className="min-w-[12rem] flex-1"><input id="p-name" value={np.name} onChange={(e) => setNp({ ...np, name: e.target.value })} className={inputClass} maxLength={255} required /></Field>
            <Button type="submit" variant="primary" disabled={!np.code.trim() || !np.name.trim()} loading={saveProduct.isPending}>Adicionar produto</Button>
            <div className="w-full"><FormError error={saveProduct.error ?? delProduct.error} fallback="Não foi possível salvar o produto." /></div>
          </form>
        )}
        <QueryView query={products} loading={<Skeleton className="h-24" />} errorTitle="Não foi possível carregar os produtos" isEmpty={(d) => d.length === 0}
          empty={<EmptyState className="py-6" title="Nenhum produto cadastrado" />}>
          {(list) => (
            <ul className="flex flex-wrap gap-2">
              {list.map((p) => (
                <li key={p.id} className="flex items-center gap-2 rounded-full border border-line py-1 pl-3 pr-1 text-caption">
                  <span className="font-medium text-fg">{p.code}</span><span className="text-fg-muted">{p.name}</span>
                  {canEdit && <button type="button" aria-label={`Excluir produto ${p.code}`} onClick={() => { if (window.confirm(`Excluir o produto ${p.code}?`)) delProduct.mutate(p.id); }}
                    className="flex size-7 items-center justify-center rounded-full text-fg-subtle hover:bg-danger-solid/10 hover:text-danger">×</button>}
                </li>
              ))}
            </ul>
          )}
        </QueryView>
      </Card>

      <Card>
        <CardHeader title="Tempo de ciclo ideal por máquina" description="PRI (ISO 22400): tempo de um ciclo na velocidade de projeto. Base do desempenho."
          action={
            <select aria-label="Máquina" value={mid} onChange={(e) => setMachineId(e.target.value)} className={clsx(inputClass, "sm:!w-auto")}>
              {plant.lines.map((l) => <optgroup key={l.id} label={l.name}>{l.machines.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</optgroup>)}
            </select>
          } />
        {!mid ? <EmptyState title="Nenhuma máquina" /> : (
          <>
            <QueryView query={mps} loading={<Skeleton className="h-24" />} errorTitle="Não foi possível carregar os produtos da máquina" isEmpty={(d) => d.length === 0}
              empty={<EmptyState className="py-6" title="Nenhum produto nesta máquina" description="Sem tempo de ciclo ideal o desempenho não é calculado." />}>
              {(list) => (
                <Table caption="Produtos da máquina">
                  <THead><tr><TH>Produto</TH><TH numeric>Ciclo ideal (s)</TH><TH numeric>Peças/ciclo</TH><TH numeric>s/peça</TH><TH>Padrão</TH>{canEdit && <TH><span className="sr-only">Ações</span></TH>}</tr></THead>
                  <TBody>{list.map((mp) => <MachineProductRow key={mp.id} mp={mp} machineId={mid} canEdit={canEdit} />)}</TBody>
                </Table>
              )}
            </QueryView>
            {canEdit && (
              <form className="mt-4 flex flex-wrap items-end gap-3 border-t border-line pt-4"
                onSubmit={(e) => { e.preventDefault(); addMp.mutate({ body: { product_id: nm.product_id, ideal_cycle_time_s: cycle, parts_per_cycle: Number(nm.ppc), is_default: nm.is_default } }, { onSuccess: () => setNm({ product_id: "", cycle: "", ppc: "1", is_default: false }) }); }}>
                <Field label="Produto" htmlFor="mp-prod" className="min-w-[12rem] flex-1">
                  <select id="mp-prod" value={nm.product_id} onChange={(e) => setNm({ ...nm, product_id: e.target.value })} className={inputClass}>
                    <option value="">Escolha…</option>
                    {available.map((p) => <option key={p.id} value={p.id}>{p.code} · {p.name}</option>)}
                  </select>
                </Field>
                <Field label="Ciclo ideal (s)" htmlFor="mp-cycle" className="w-32"><input id="mp-cycle" inputMode="decimal" value={nm.cycle} onChange={(e) => setNm({ ...nm, cycle: e.target.value })} className={inputClass} /></Field>
                <Field label="Peças/ciclo" htmlFor="mp-ppc" className="w-28"><input id="mp-ppc" inputMode="numeric" value={nm.ppc} onChange={(e) => setNm({ ...nm, ppc: e.target.value })} className={inputClass} /></Field>
                <label className="flex min-h-10 items-center gap-2 text-body"><input type="checkbox" checked={nm.is_default} onChange={(e) => setNm({ ...nm, is_default: e.target.checked })} className="size-4" />Padrão</label>
                <Button type="submit" variant="primary" disabled={!nm.product_id || !(cycle > 0) || !(Number(nm.ppc) >= 1)} loading={addMp.isPending}>Vincular</Button>
                <div className="w-full"><FormError error={addMp.error} fallback="Não foi possível vincular o produto." /></div>
              </form>
            )}
          </>
        )}
      </Card>
    </div>
  );
}

// ── reason codes ──────────────────────────────────────────────────

function ReasonsTab({ canEdit }: { canEdit: boolean }) {
  const codes = useReasonCodes(true);
  const create = useCreateReasonCode();
  const update = useUpdateReasonCode();
  const [f, setF] = useState({ code: "", category: "EQP", label: "", planned: false, oee_loss: "availability" as OeeLossKind });
  const categories = useMemo(() => [...new Set((codes.data ?? []).map((c) => c.category))].filter((c) => c !== "UNK").sort(), [codes.data]);
  const codeOk = REASON_CODE_RE.test(f.code);

  return (
    <Card>
      <CardHeader title="Motivos de parada" description="Códigos padrão (somente leitura) + extensões da sua empresa. Formato: CAT-NN ou CAT-NN-SUFIXO (ex.: EQP-01-HID)." />
      <ReadOnlyNote canEdit={canEdit} />
      {canEdit && (
        <form className="mb-5 grid gap-3 rounded-xl border border-line bg-surface-2/40 p-3 sm:grid-cols-2 lg:grid-cols-6"
          onSubmit={(e) => { e.preventDefault(); create.mutate({ ...f, code: f.code.trim().toUpperCase(), label: f.label.trim() }, { onSuccess: () => setF({ ...f, code: "", label: "" }) }); }}>
          <Field label="Código" htmlFor="rc-code" error={f.code && !codeOk ? "Formato CAT-NN" : undefined}>
            <input id="rc-code" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.toUpperCase() })} className={inputClass} placeholder="EQP-01-HID" />
          </Field>
          <Field label="Categoria" htmlFor="rc-cat">
            <select id="rc-cat" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} className={inputClass}>
              {categories.map((c) => <option key={c} value={c}>{c} · {categoryLabel(c)}</option>)}
            </select>
          </Field>
          <Field label="Descrição" htmlFor="rc-label" className="lg:col-span-2"><input id="rc-label" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} className={inputClass} maxLength={255} /></Field>
          <Field label="Perda no OEE" htmlFor="rc-loss">
            <select id="rc-loss" value={f.oee_loss} onChange={(e) => setF({ ...f, oee_loss: e.target.value as OeeLossKind })} className={inputClass}>
              {Object.entries(LOSS_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </Field>
          <div className="flex flex-col justify-end gap-2">
            <label className="flex items-center gap-2 text-body"><input type="checkbox" checked={f.planned} onChange={(e) => setF({ ...f, planned: e.target.checked })} className="size-4" />Planejada</label>
            <Button type="submit" variant="primary" disabled={!codeOk || !f.label.trim()} loading={create.isPending}>Adicionar</Button>
          </div>
          <div className="sm:col-span-2 lg:col-span-6"><FormError error={create.error ?? update.error} fallback="Não foi possível salvar o motivo." /></div>
        </form>
      )}
      <QueryView query={codes} loading={<LoadingRegion><Skeleton className="h-48" /></LoadingRegion>} errorTitle="Não foi possível carregar os motivos">
        {(list) => (
          <Table caption="Motivos de parada">
            <THead><tr><TH>Código</TH><TH>Descrição</TH><TH>Categoria</TH><TH>Perda</TH><TH>Origem</TH><TH>Situação</TH></tr></THead>
            <TBody>
              {list.slice().sort((a, b) => a.code.localeCompare(b.code)).map((r) => {
                const own = !r.is_default && r.tenant_id != null;
                return (
                  <TR key={r.id} className={clsx(!r.is_active && "opacity-60")}>
                    <TD className="font-mono text-caption">{r.code}</TD>
                    <TD>{r.label}{r.planned && <Badge tone="info" className="ml-2">Planejada</Badge>}</TD>
                    <TD className="text-fg-muted">{categoryLabel(r.category)}</TD>
                    <TD className="text-fg-muted">{LOSS_LABEL[r.oee_loss] ?? r.oee_loss}</TD>
                    <TD>{own ? <Badge tone="accent">Empresa</Badge> : <Badge>Padrão</Badge>}</TD>
                    <TD>
                      {own && canEdit ? (
                        <Button size="sm" variant="ghost" onClick={() => update.mutate({ id: r.id, body: { is_active: !r.is_active } })}>
                          {r.is_active ? "Desativar" : "Ativar"}
                        </Button>
                      ) : (r.is_active ? "Ativo" : "Inativo")}
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        )}
      </QueryView>
    </Card>
  );
}

export default function ProductionSettingsPage() {
  const { tree, plant } = usePlant();
  const role = useAuthStore((s) => s.user?.role);
  const canEdit = role === "admin" || role === "superadmin";
  const [params, setParams] = useSearchParams();
  const tab = (TABS.some((t) => t.value === params.get("tab")) ? params.get("tab") : "shifts") as TabKey;

  return (
    <div className="mx-auto max-w-6xl space-y-5 animate-rise-in">
      <div>
        <h2 className="text-title text-fg">Configurações de produção</h2>
        <p className="text-caption text-fg-subtle">Calendário, produtos e motivos que alimentam o cálculo do OEE.</p>
      </div>
      <Tabs id="settings" label="Seções" items={TABS} value={tab} onChange={(t) => setParams({ tab: t }, { replace: true })} />
      <TabPanel id="settings" activeValue={tab}>
        {tree.isError ? <ErrorState error={tree.error} onRetry={() => void tree.refetch()} />
          : tab === "reasons" ? <ReasonsTab canEdit={canEdit} />
          : tree.isPending ? <Skeleton className="h-64 rounded-xl" />
          : !plant ? <Card><EmptyState title="Nenhuma planta cadastrada" /></Card>
          : tab === "shifts" ? <ShiftsTab plant={plant} canEdit={canEdit} />
          : tab === "calendar" ? <CalendarTab plant={plant} canEdit={canEdit} />
          : <ProductsTab plant={plant} canEdit={canEdit} />}
      </TabPanel>
    </div>
  );
}
