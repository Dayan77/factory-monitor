import clsx from "clsx";
import { inputClass } from "@/components/ui/Field";
import { Tabs } from "@/components/ui/Tabs";
import { PERIOD_LABELS, type PeriodPreset } from "@/utils/period";

const PRESETS: PeriodPreset[] = ["shift", "today", "yesterday", "7d", "30d", "custom"];

interface Props {
  value: PeriodPreset;
  onChange: (p: PeriodPreset) => void;
  from?: string;
  to?: string;
  onCustom?: (from: string, to: string) => void;
  /** Hide presets (e.g. "custom") where not useful. */
  presets?: PeriodPreset[];
  id?: string;
}

/** Period presets (turno atual, hoje, ontem, 7/30 dias, personalizado) + date inputs for custom. */
export default function PeriodPicker({ value, onChange, from = "", to = "", onCustom, presets = PRESETS, id = "period" }: Props) {
  return (
    <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2">
      <Tabs id={id} label="Período" size="sm" value={value} onChange={onChange}
        items={presets.map((p) => ({ value: p, label: PERIOD_LABELS[p] }))} />
      {value === "custom" && onCustom && (
        <div className="flex items-center gap-2">
          <label className="sr-only" htmlFor={`${id}-from`}>Data inicial</label>
          <input id={`${id}-from`} type="date" value={from} onChange={(e) => onCustom(e.target.value, to)}
            className={clsx(inputClass, "!w-auto min-h-9 py-1")} />
          <span className="text-caption text-fg-subtle">até</span>
          <label className="sr-only" htmlFor={`${id}-to`}>Data final</label>
          <input id={`${id}-to`} type="date" value={to} onChange={(e) => onCustom(from, e.target.value)}
            className={clsx(inputClass, "!w-auto min-h-9 py-1")} />
        </div>
      )}
    </div>
  );
}
