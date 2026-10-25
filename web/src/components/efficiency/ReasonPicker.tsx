import clsx from "clsx";
import type { ReasonGroup } from "@/utils/efficiency";

interface Props {
  groups: ReasonGroup[];
  value: string | null;
  suggested?: string | null;
  onChange: (code: string) => void;
  /** Accessible name. */
  label?: string;
}

/**
 * Reason picker for the operator tablet: reasons grouped by category, ≥56px touch targets,
 * the suggested reason marked (and preselected by the caller). Radio-group semantics.
 */
export default function ReasonPicker({ groups, value, suggested, onChange, label = "Motivo da parada" }: Props) {
  return (
    <div role="radiogroup" aria-label={label} className="space-y-4">
      {groups.map((g) => (
        <fieldset key={g.category} className="min-w-0">
          <legend className="mb-2 text-overline uppercase text-fg-subtle">{g.label}</legend>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {g.codes.map((c) => {
              const selected = value === c.code;
              return (
                <button
                  key={c.code}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  onClick={() => onChange(c.code)}
                  className={clsx(
                    "flex min-h-14 items-center gap-3 rounded-xl border-2 px-4 py-2 text-left transition-colors duration-150",
                    selected ? "border-accent bg-accent/10" : "border-line bg-surface hover:border-line-strong hover:bg-surface-2",
                  )}
                >
                  <span aria-hidden="true" className={clsx(
                    "flex size-5 shrink-0 items-center justify-center rounded-full border-2",
                    selected ? "border-accent bg-accent" : "border-line-strong",
                  )}>
                    {selected && <span className="size-2 rounded-full bg-accent-fg" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-body font-medium text-fg">{c.label}</span>
                    <span className="block text-caption text-fg-subtle">{c.code}</span>
                  </span>
                  {suggested === c.code && (
                    <span className="shrink-0 rounded-full bg-info-solid/15 px-2 py-0.5 text-caption font-medium text-info">Sugerido</span>
                  )}
                </button>
              );
            })}
          </div>
        </fieldset>
      ))}
    </div>
  );
}
