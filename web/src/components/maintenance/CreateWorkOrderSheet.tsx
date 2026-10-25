import { useEffect, useState } from "react";
import clsx from "clsx";
import Sheet from "@/components/ui/Sheet";
import Button from "@/components/ui/Button";
import { Field, inputClass } from "@/components/ui/Field";
import { useCreateWorkOrder, usePositionsIndex } from "@/hooks/useMaintenance";
import { usePlantTree } from "@/hooks/useAssets";
import type { WoType, WoUrgency, WorkOrder } from "@/services/maintenanceTypes";
import { apiErrorMessage } from "@/utils/errors";
import { WO_TYPE_LABEL } from "@/utils/maintenance";

const TYPES: WoType[] = ["corrective", "preventive", "predictive", "improvement"];

/** "Solicitar manutenção" / "Nova OS": creates an `open` request (planner approves) — workflows §4. */
export default function CreateWorkOrderSheet({ open, onClose, onCreated, machineId, positionId, allowEmergency = false }: {
  open: boolean;
  onClose: () => void;
  onCreated: (wo: WorkOrder) => void;
  machineId?: string;
  positionId?: string | null;
  allowEmergency?: boolean;
}) {
  const tree = usePlantTree();
  const create = useCreateWorkOrder();
  const [machine, setMachine] = useState(machineId ?? "");
  const [position, setPosition] = useState(positionId ?? "");
  const [type, setType] = useState<WoType>("corrective");
  const [urgency, setUrgency] = useState<WoUrgency>("deferred");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [hours, setHours] = useState("");
  const [stops, setStops] = useState(false);
  const positions = usePositionsIndex(open && machine ? [machine] : []);
  const positionList = [...positions.values()].filter((p) => p.machine_id === machine);

  useEffect(() => {
    if (!open) return;
    setMachine(machineId ?? "");
    setPosition(positionId ?? "");
    setType("corrective");
    setUrgency("deferred");
    setTitle("");
    setDescription("");
    setHours("");
    setStops(false);
    create.reset();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const est = hours.trim() ? Number(hours.replace(",", ".")) : null;
  const invalid = !machine || title.trim().length < 3 || (est != null && (Number.isNaN(est) || est < 0));

  function submit() {
    if (invalid) return;
    create.mutate({
      type, urgency: type === "corrective" ? urgency : null, machine_id: machine, position_id: position || null,
      title: title.trim(), description: description.trim() || null, estimated_hours: est, stops_machine: stops,
    }, { onSuccess: (wo) => { onCreated(wo); onClose(); } });
  }

  return (
    <Sheet open={open} onClose={onClose} size="lg" title="Solicitar manutenção" description="A solicitação entra como “Solicitada” para o planejador aprovar."
      footer={
        <div className="space-y-2">
          {create.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(create.error, "Não foi possível abrir a OS.")}</p>}
          <Button variant="primary" size="lg" className="w-full" disabled={invalid} loading={create.isPending} onClick={submit}>Abrir solicitação</Button>
        </div>
      }>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Máquina" htmlFor="cwo-machine">
            <select id="cwo-machine" value={machine} onChange={(e) => { setMachine(e.target.value); setPosition(""); }} className={inputClass}>
              <option value="">Escolha a máquina…</option>
              {(tree.data ?? []).flatMap((p) => p.lines).map((l) => (
                <optgroup key={l.id} label={l.name}>{l.machines.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</optgroup>
              ))}
            </select>
          </Field>
          <Field label="Posição (opcional)" htmlFor="cwo-pos">
            <select id="cwo-pos" value={position} onChange={(e) => setPosition(e.target.value)} className={inputClass} disabled={!machine}>
              <option value="">Máquina inteira / não sei</option>
              {positionList.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
        </div>
        <fieldset>
          <legend className="mb-1.5 text-caption font-medium text-fg-muted">Tipo</legend>
          <div className="flex flex-wrap gap-2">
            {TYPES.map((t) => (
              <label key={t} className={clsx("flex min-h-10 cursor-pointer items-center gap-2 rounded-lg border px-3 text-body", type === t ? "border-accent bg-accent/10 text-fg" : "border-line text-fg-muted")}>
                <input type="radio" name="cwo-type" checked={type === t} onChange={() => setType(t)} className="accent-[rgb(var(--c-accent))]" />
                {WO_TYPE_LABEL[t]}
              </label>
            ))}
          </div>
        </fieldset>
        {type === "corrective" && allowEmergency && (
          <label className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border border-line px-3">
            <input type="checkbox" checked={urgency === "emergency"} onChange={(e) => setUrgency(e.target.checked ? "emergency" : "deferred")} className="size-5 accent-[rgb(var(--c-accent))]" />
            <span className="text-body text-fg">Emergencial (parou a produção — chama o plantão)</span>
          </label>
        )}
        <Field label="Título" htmlFor="cwo-title" hint="O que está acontecendo, em poucas palavras.">
          <input id="cwo-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} className={inputClass} placeholder="Ex.: vazamento de ar no cilindro A2" />
        </Field>
        <Field label="Descrição (opcional)" htmlFor="cwo-desc">
          <textarea id="cwo-desc" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={4000} className={inputClass} />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Horas estimadas (opcional)" htmlFor="cwo-hours">
            <input id="cwo-hours" inputMode="decimal" value={hours} onChange={(e) => setHours(e.target.value)} className={inputClass} />
          </Field>
          <label className="flex min-h-11 cursor-pointer items-center gap-3 self-end rounded-lg border border-line px-3">
            <input type="checkbox" checked={stops} onChange={(e) => setStops(e.target.checked)} className="size-5 accent-[rgb(var(--c-accent))]" />
            <span className="text-body text-fg">Precisa parar a máquina</span>
          </label>
        </div>
      </form>
    </Sheet>
  );
}
