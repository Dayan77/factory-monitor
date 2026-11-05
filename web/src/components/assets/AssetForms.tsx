import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Button from "@/components/ui/Button";
import { Field, inputClass } from "@/components/ui/Field";
import Sheet from "@/components/ui/Sheet";
import { usePlantTree } from "@/hooks/useAssets";
import { linesApi, machinesApi } from "@/services/api";
import type { Line, Machine } from "@/services/types";
import { apiErrorMessage } from "@/utils/errors";

/** Lists that show lines/machines: refreshed after any create or edit. */
function useInvalidateAssets() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["lines"] });
    void qc.invalidateQueries({ queryKey: ["machines"] });
    void qc.invalidateQueries({ queryKey: ["plants"] });
  };
}

const blankToNull = (v: string) => (v.trim() ? v.trim() : null);

// ── Line ──────────────────────────────────────────────────────────

export function LineFormSheet({ open, onClose, line }: { open: boolean; onClose: () => void; line?: Line | null }) {
  const editing = !!line;
  const tree = usePlantTree();
  const plants = tree.data ?? [];
  const invalidate = useInvalidateAssets();
  const [f, setF] = useState({ name: "", location: "", description: "", plant_id: "", is_active: true });
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTouched(false);
    setF({
      name: line?.name ?? "",
      location: line?.location ?? "",
      description: line?.description ?? "",
      plant_id: line?.plant_id ?? "",
      is_active: line?.is_active ?? true,
    });
  }, [open, line]);

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: f.name.trim(),
        location: blankToNull(f.location),
        description: blankToNull(f.description),
        ...(f.plant_id ? { plant_id: f.plant_id } : {}),
      };
      return editing ? linesApi.update(line!.id, { ...body, is_active: f.is_active }) : linesApi.create(body);
    },
    onSuccess: () => { invalidate(); onClose(); },
  });
  useEffect(() => { if (open) save.reset(); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const nameErr = !f.name.trim() ? "Informe o nome da linha." : undefined;
  function submit(e: FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (!nameErr) save.mutate();
  }

  return (
    <Sheet open={open} onClose={onClose} title={editing ? `Editar linha ${line!.name}` : "Nova linha de produção"}
      description={editing ? undefined : "Depois cadastre as máquinas da linha e escolha a máquina de saída (a que conta as peças)."}
      footer={
        <div className="flex gap-2">
          <Button variant="primary" type="submit" form="line-form" loading={save.isPending}>{editing ? "Salvar" : "Criar linha"}</Button>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
        </div>
      }>
      <form id="line-form" onSubmit={submit} className="space-y-4" noValidate>
        <Field label="Nome da linha" htmlFor="line-name" error={touched ? nameErr : undefined}>
          <input id="line-name" value={f.name} maxLength={255} onChange={(e) => setF({ ...f, name: e.target.value })} className={inputClass} placeholder="Ex.: Linha LED Driver" />
        </Field>
        {plants.length > 1 && (
          <Field label="Planta" htmlFor="line-plant">
            <select id="line-plant" value={f.plant_id} onChange={(e) => setF({ ...f, plant_id: e.target.value })} className={inputClass}>
              {!editing && <option value="">Planta principal</option>}
              {plants.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
        )}
        <Field label="Local" htmlFor="line-location" hint="Opcional. Ex.: Galpão 2">
          <input id="line-location" value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} className={inputClass} />
        </Field>
        <Field label="Descrição" htmlFor="line-description" hint="Opcional.">
          <textarea id="line-description" rows={3} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} className={inputClass} />
        </Field>
        {editing && (
          <label className="flex min-h-10 items-center gap-3 text-body text-fg">
            <input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} className="size-5 accent-[rgb(var(--c-accent))]" />
            Linha ativa
          </label>
        )}
        {save.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(save.error, "Não foi possível salvar a linha.")}</p>}
      </form>
    </Sheet>
  );
}

// ── Machine ───────────────────────────────────────────────────────

export function MachineFormSheet({ open, onClose, machine, lines, defaultLineId }: {
  open: boolean; onClose: () => void; machine?: Machine | null; lines: Line[]; defaultLineId?: string;
}) {
  const editing = !!machine;
  const invalidate = useInvalidateAssets();
  const [f, setF] = useState({ line_id: "", name: "", model: "", serial_number: "", ideal_cycle_time_s: "", micro_stop_threshold_s: "120" });
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTouched(false);
    setF({
      line_id: machine?.line_id ?? defaultLineId ?? (lines.length === 1 ? lines[0].id : ""),
      name: machine?.name ?? "",
      model: machine?.model ?? "",
      serial_number: machine?.serial_number ?? "",
      ideal_cycle_time_s: machine?.ideal_cycle_time_s != null ? String(machine.ideal_cycle_time_s) : "",
      micro_stop_threshold_s: String(machine?.micro_stop_threshold_s ?? 120),
    });
  }, [open, machine, defaultLineId, lines]);

  const cycle = f.ideal_cycle_time_s.trim() ? Number(f.ideal_cycle_time_s.replace(",", ".")) : null;
  const micro = Number(f.micro_stop_threshold_s);
  const errs: Record<string, string> = {};
  if (!f.line_id) errs.line = "Escolha a linha.";
  if (!f.name.trim()) errs.name = "Informe o nome da máquina.";
  if (cycle !== null && !(Number.isFinite(cycle) && cycle > 0)) errs.cycle = "Tempo de ciclo deve ser maior que zero.";
  if (!(Number.isInteger(micro) && micro >= 30 && micro <= 300)) errs.micro = "Entre 30 e 300 segundos.";

  const save = useMutation({
    mutationFn: () => {
      const body = {
        line_id: f.line_id,
        name: f.name.trim(),
        model: blankToNull(f.model),
        serial_number: blankToNull(f.serial_number),
        ideal_cycle_time_s: cycle,
        micro_stop_threshold_s: micro,
      };
      return editing ? machinesApi.update(machine!.id, body) : machinesApi.create(body);
    },
    onSuccess: () => { invalidate(); onClose(); },
  });
  useEffect(() => { if (open) save.reset(); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  function submit(e: FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (Object.keys(errs).length === 0) save.mutate();
  }
  const err = (k: string) => (touched ? errs[k] : undefined);

  return (
    <Sheet open={open} onClose={onClose} title={editing ? `Editar máquina ${machine!.name}` : "Nova máquina"}
      description={editing ? undefined : "Os sensores (tags) da máquina chegam pelo gateway; aqui você cadastra o ativo."}
      footer={
        <div className="flex gap-2">
          <Button variant="primary" type="submit" form="machine-form" loading={save.isPending} disabled={lines.length === 0}>{editing ? "Salvar" : "Criar máquina"}</Button>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
        </div>
      }>
      <form id="machine-form" onSubmit={submit} className="space-y-4" noValidate>
        {lines.length === 0 ? (
          <p role="alert" className="text-body text-fg-muted">Cadastre uma linha de produção antes de cadastrar máquinas.</p>
        ) : (
          <Field label="Linha" htmlFor="machine-line" error={err("line")}>
            <select id="machine-line" value={f.line_id} onChange={(e) => setF({ ...f, line_id: e.target.value })} className={inputClass}>
              <option value="">— escolha —</option>
              {lines.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
          </Field>
        )}
        <Field label="Nome da máquina" htmlFor="machine-name" error={err("name")}>
          <input id="machine-name" value={f.name} maxLength={255} onChange={(e) => setF({ ...f, name: e.target.value })} className={inputClass} placeholder="Ex.: M1 Hipot" />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Modelo" htmlFor="machine-model" hint="Opcional.">
            <input id="machine-model" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} className={inputClass} />
          </Field>
          <Field label="Nº de série" htmlFor="machine-serial" hint="Opcional.">
            <input id="machine-serial" value={f.serial_number} onChange={(e) => setF({ ...f, serial_number: e.target.value })} className={inputClass} />
          </Field>
          <Field label="Tempo de ciclo ideal (s)" htmlFor="machine-cycle" error={err("cycle")} hint="Base da Performance do OEE (ISO 22400). Opcional.">
            <input id="machine-cycle" inputMode="decimal" value={f.ideal_cycle_time_s} onChange={(e) => setF({ ...f, ideal_cycle_time_s: e.target.value })} className={inputClass} placeholder="Ex.: 12,5" />
          </Field>
          <Field label="Limite de micro-parada (s)" htmlFor="machine-micro" error={err("micro")} hint="Paradas menores contam como micro-paradas (30–300 s).">
            <input id="machine-micro" type="number" min={30} max={300} step={1} value={f.micro_stop_threshold_s} onChange={(e) => setF({ ...f, micro_stop_threshold_s: e.target.value })} className={inputClass} />
          </Field>
        </div>
        {save.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(save.error, "Não foi possível salvar a máquina.")}</p>}
      </form>
    </Sheet>
  );
}
