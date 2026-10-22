import clsx from "clsx";
import Badge from "@/components/ui/Badge";
import { Progress } from "@/components/ui/LifeBar";
import type { PlanState, TriggerMeasure, WoState, WoType, WoUrgency } from "@/services/maintenanceTypes";
import { formatPct } from "@/utils/format";
import { PLAN_STATE_LABEL, TRIGGER_LABEL, URGENCY_LABEL, WO_STATE_LABEL, WO_STATE_TONE, WO_TYPE_LABEL } from "@/utils/maintenance";

/** Plan state colours (workflows §2): overdue red, due orange, due_soon yellow, ok green. */
const PLAN_CLASS: Record<PlanState, { box: string; dot: string; fill: string; text: string }> = {
  overdue: { box: "bg-danger-solid/15 text-danger", dot: "bg-danger-solid", fill: "bg-danger-solid", text: "text-danger" },
  due: { box: "bg-amber-500/15 text-amber-400", dot: "bg-amber-500", fill: "bg-amber-500", text: "text-amber-400" },
  due_soon: { box: "bg-yellow-500/15 text-yellow-400", dot: "bg-yellow-500", fill: "bg-yellow-500", text: "text-yellow-400" },
  ok: { box: "bg-ok-solid/15 text-ok", dot: "bg-ok-solid", fill: "bg-ok-solid", text: "text-fg" },
};

export function PlanStateBadge({ state, className }: { state: PlanState; className?: string }) {
  const c = PLAN_CLASS[state];
  return (
    <span data-state={state} className={clsx("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-caption font-medium", c.box, className)}>
      <span aria-hidden="true" className={clsx("size-1.5 rounded-full", c.dot, state === "overdue" && "animate-pulse")} />
      {PLAN_STATE_LABEL[state]}
    </span>
  );
}

/** Consumed fraction of a plan (may exceed 100 % — the number is never truncated, the bar is). */
export function ConsumedBar({ consumed, state, trigger, label, className }: {
  consumed: number | null;
  state: PlanState;
  trigger?: TriggerMeasure | null;
  label: string;
  className?: string;
}) {
  const c = PLAN_CLASS[state];
  if (consumed == null) {
    return <span className={clsx("text-caption text-fg-subtle", className)}>— sem gatilho avaliável</span>;
  }
  return (
    <div className={clsx("flex min-w-[8rem] items-center gap-2", className)}>
      <Progress value={consumed} label={label} fillClassName={c.fill} className="flex-1" />
      <span className={clsx("shrink-0 text-right text-caption font-semibold tabular", c.text)}>
        {formatPct(consumed)}
        {trigger && <span className="ml-1 font-normal text-fg-subtle">({TRIGGER_LABEL[trigger]})</span>}
      </span>
    </div>
  );
}

export function WoStateBadge({ state, className }: { state: WoState; className?: string }) {
  return (
    <Badge tone={WO_STATE_TONE[state]} dot pulse={state === "in_progress"} className={className}>
      <span data-wo-state={state}>{WO_STATE_LABEL[state]}</span>
    </Badge>
  );
}

export function WoTypeBadge({ type, urgency, compact = false }: { type: WoType; urgency?: WoUrgency | null; compact?: boolean }) {
  if (type === "corrective" && urgency === "emergency") {
    return <Badge tone="danger" dot pulse title="Corretiva emergencial">{compact ? URGENCY_LABEL.emergency : `Corretiva · ${URGENCY_LABEL.emergency}`}</Badge>;
  }
  return <Badge tone={type === "corrective" ? "warn" : type === "improvement" ? "info" : "neutral"}>{WO_TYPE_LABEL[type]}</Badge>;
}

export function OverdueBadge() {
  return <Badge tone="danger" dot>Atrasada</Badge>;
}

export function BreakInBadge() {
  return (
    <Badge tone="info" title="Programada depois do congelamento da semana (segunda 00:00): não conta no cumprimento da programação.">
      Encaixe
    </Badge>
  );
}
