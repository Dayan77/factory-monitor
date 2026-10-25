import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import Button from "@/components/ui/Button";
import { Field, inputClass } from "@/components/ui/Field";
import { useFailureCodes, usePositionsIndex } from "@/hooks/useMaintenance";
import type { FailureCodes, FailurePatchBody, FailureRecord, FailureSeverity } from "@/services/maintenanceTypes";
import { groupCodes, ISO_CODES, SEVERITY_LABEL, shortModes } from "@/utils/maintenance";

const SEVERITIES: FailureSeverity[] = ["critical", "degraded", "incipient"];

/** ISO 14224 lists: the server's when available, else the normative lists of domain.md §5.5 (flagged). */
export function useIsoCodes(): { codes: FailureCodes; fromServer: boolean; loading: boolean } {
  const q = useFailureCodes();
  const d = q.data;
  const ok = !!d && d.modes.length > 0;
  return {
    codes: ok ? {
      modes: d.modes,
      mechanisms: d.mechanisms.length ? d.mechanisms : ISO_CODES.mechanisms,
      causes: d.causes.length ? d.causes : ISO_CODES.causes,
      detection_methods: d.detection_methods.length ? d.detection_methods : ISO_CODES.detection_methods,
    } : ISO_CODES,
    fromServer: ok,
    loading: q.isPending,
  };
}

function GroupedSelect({ id, value, onChange, list }: { id: string; value: string; onChange: (v: string) => void; list: FailureCodes["mechanisms"] }) {
  const groups = groupCodes(list);
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={clsx(inputClass, "min-h-12")}>
      <option value="">Escolha…</option>
      {groups.map((g) => g.group ? (
        <optgroup key={g.group} label={g.group}>{g.items.map((c) => <option key={c.code} value={c.code}>{c.code} — {c.label}</option>)}</optgroup>
      ) : g.items.map((c) => <option key={c.code} value={c.code}>{c.code} — {c.label}</option>))}
    </select>
  );
}

/**
 * Failure data (ISO 14224): severity, mode (short list by component type first), mechanism and cause
 * (may stay 6.4 / 5.5 for the planner to complete), detection method and the failed position.
 */
export default function FailureForm({ failure, componentType, busy, error, onSave, submitLabel = "Salvar falha", idPrefix = "ff" }: {
  failure: FailureRecord;
  componentType?: string | null;
  busy: boolean;
  error?: string | null;
  onSave: (body: FailurePatchBody) => void;
  submitLabel?: string;
  idPrefix?: string;
}) {
  const { codes, fromServer } = useIsoCodes();
  const positions = usePositionsIndex([failure.machine_id]);
  const positionList = useMemo(() => [...positions.values()].filter((p) => p.machine_id === failure.machine_id), [positions, failure.machine_id]);
  const [severity, setSeverity] = useState<FailureSeverity>(failure.severity ?? "critical");
  const [mode, setMode] = useState(failure.failure_mode ?? "");
  const [mechanism, setMechanism] = useState(failure.failure_mechanism ?? "6.4");
  const [cause, setCause] = useState(failure.failure_cause ?? "5.5");
  const [detection, setDetection] = useState(failure.detection_method ?? "");
  const [position, setPosition] = useState(failure.position_id ?? "");
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    setSeverity(failure.severity ?? "critical");
    setMode(failure.failure_mode ?? "");
    setMechanism(failure.failure_mechanism ?? "6.4");
    setCause(failure.failure_cause ?? "5.5");
    setDetection(failure.detection_method ?? "");
    setPosition(failure.position_id ?? "");
  }, [failure.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const type = componentType ?? positions.get(position)?.component_type ?? failure.component_type ?? null;
  const short = shortModes(codes.modes, type);
  const visibleModes = showAll || short.length === 0 ? codes.modes : short;
  const invalid = !mode || !mechanism || !cause || !severity;

  return (
    <form className="space-y-5" onSubmit={(e) => {
      e.preventDefault();
      if (invalid) return;
      onSave({ severity, failure_mode: mode, failure_mechanism: mechanism, failure_cause: cause, detection_method: detection || null, position_id: position || null });
    }}>
      {!fromServer && (
        <p className="text-caption text-fg-subtle">Listas ISO 14224 da especificação (o servidor não forneceu as listas).</p>
      )}
      {positionList.length > 0 && (
        <Field label="Posição que falhou" htmlFor={`${idPrefix}-pos`}>
          <select id={`${idPrefix}-pos`} value={position} onChange={(e) => setPosition(e.target.value)} className={clsx(inputClass, "min-h-12")}>
            <option value="">Máquina (posição desconhecida)</option>
            {positionList.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </Field>
      )}
      <fieldset>
        <legend className="mb-2 text-caption font-medium text-fg-muted">Severidade</legend>
        <div className="grid gap-2 sm:grid-cols-3">
          {SEVERITIES.map((s) => (
            <label key={s} className={clsx("flex min-h-14 cursor-pointer items-center gap-3 rounded-xl border-2 px-3 py-2 text-body",
              severity === s ? "border-accent bg-accent/10 text-fg" : "border-line text-fg-muted")}>
              <input type="radio" name={`${idPrefix}-sev`} checked={severity === s} onChange={() => setSeverity(s)} className="size-5 accent-[rgb(var(--c-accent))]" />
              {SEVERITY_LABEL[s]}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset>
        <legend className="mb-2 text-caption font-medium text-fg-muted">Modo de falha (como se manifestou)</legend>
        <div role="radiogroup" aria-label="Modo de falha" className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {visibleModes.map((m) => (
            <button key={m.code} type="button" role="radio" aria-checked={mode === m.code} onClick={() => setMode(m.code)}
              className={clsx("flex min-h-14 flex-col items-start justify-center rounded-xl border-2 px-3 py-1.5 text-left",
                mode === m.code ? "border-accent bg-accent/10" : "border-line hover:border-line-strong")}>
              <span className="text-caption font-bold text-fg">{m.code}</span>
              <span className="text-caption text-fg-muted">{m.label}</span>
            </button>
          ))}
        </div>
        {short.length > 0 && (
          <button type="button" className="mt-2 min-h-10 text-caption font-medium text-ok hover:underline" onClick={() => setShowAll((v) => !v)}>
            {showAll ? "Mostrar só os modos comuns deste componente" : "Ver todos os modos"}
          </button>
        )}
      </fieldset>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Mecanismo (processo físico)" htmlFor={`${idPrefix}-mech`} hint="Se não souber, deixe 6.4 — o planejador completa.">
          <GroupedSelect id={`${idPrefix}-mech`} value={mechanism} onChange={setMechanism} list={codes.mechanisms} />
        </Field>
        <Field label="Causa raiz" htmlFor={`${idPrefix}-cause`} hint="Se não souber, deixe 5.5.">
          <GroupedSelect id={`${idPrefix}-cause`} value={cause} onChange={setCause} list={codes.causes} />
        </Field>
      </div>
      <Field label="Como foi detectada" htmlFor={`${idPrefix}-det`}>
        <select id={`${idPrefix}-det`} value={detection} onChange={(e) => setDetection(e.target.value)} className={clsx(inputClass, "min-h-12")}>
          <option value="">Escolha…</option>
          {codes.detection_methods.map((d) => <option key={d.code} value={d.code}>{d.label}</option>)}
        </select>
      </Field>
      {error && <p role="alert" className="text-caption text-danger">{error}</p>}
      <Button type="submit" variant="primary" size="lg" className="min-h-14 w-full" loading={busy} disabled={invalid}>{submitLabel}</Button>
    </form>
  );
}
