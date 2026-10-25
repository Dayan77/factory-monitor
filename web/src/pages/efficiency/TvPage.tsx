import { useLayoutEffect, useMemo } from "react";
import { useThemeStore } from "@/store/theme";
import { Link, useParams } from "react-router-dom";
import clsx from "clsx";
import KpiRing from "@/components/efficiency/KpiRing";
import ApqBreakdown from "@/components/efficiency/ApqBreakdown";
import { statusLabel } from "@/components/MachineStatusBadge";
import { useDowntime, useOverview, usePlant, useReasonCodes } from "@/hooks/useEfficiency";
import { useNow } from "@/hooks/useNow";
import { useRealtimeConnection } from "@/hooks/useWebSocket";
import { useAuthStore } from "@/store/auth";
import { useRealtimeStore } from "@/store/realtime";
import type { MachineStatus } from "@/services/types";
import { formatDec1, formatInt } from "@/utils/format";
import { formatTimer, reasonLabel } from "@/utils/efficiency";
import DemoBanner from "@/components/DemoBanner";

const STATUS_TILE: Record<MachineStatus, string> = {
  running: "border-ok-solid bg-ok-solid/15 text-ok",
  stopped: "border-warn-solid bg-warn-solid/15 text-warn",
  error: "border-danger-solid bg-danger-solid/20 text-danger",
  maintenance: "border-info-solid bg-info-solid/15 text-info",
  unknown: "border-line-strong bg-surface-2 text-fg-muted",
};

/**
 * Forces the dark theme while mounted (TV on the shop floor). Layout effect: the user's theme is
 * restored in the same commit that unmounts the page, so the next page never paints dark.
 */
export function useForceDark() {
  useLayoutEffect(() => {
    const el = document.documentElement;
    el.setAttribute("data-theme", "dark");
    return () => el.setAttribute("data-theme", useThemeStore.getState().theme);
  }, []);
}

/** Andon / TV mode for one line: huge OEE, cadence, machine status, current stop timer. */
export default function TvPage() {
  useForceDark();
  const { lineId = "" } = useParams();
  const tenantId = useAuthStore((s) => s.user?.tenant_id);
  useRealtimeConnection(tenantId);
  const ws = useRealtimeStore((s) => s.status);
  const { tree, plant } = usePlant();
  const overview = useOverview(plant?.id, 30_000);
  const open = useDowntime({ line_id: lineId, open: true }, 15_000);
  const reasons = useReasonCodes();
  const now = useNow(1000);

  const line = plant?.lines.find((l) => l.id === lineId) ?? null;
  const ov = overview.data?.lines.find((l) => l.line_id === lineId) ?? null;
  const oee = ov?.oee_current_shift ?? ov?.oee ?? null;
  const openStops = useMemo(() => (open.data ?? []).filter((e) => e.end == null).sort((a, b) => a.start.localeCompare(b.start)), [open.data]);
  const current = openStops[0] ?? null;
  const stopFor = useMemo(() => new Map(openStops.map((e) => [e.machine_id, e])), [openStops]);
  const clock = new Date(now).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", timeZone: plant?.timezone });

  return (
    <main className="min-h-screen bg-canvas text-fg" data-testid="tv-mode">
      <DemoBanner compact />
      <div className="p-4 sm:p-8">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0">
          <h1 className="truncate text-[clamp(1.75rem,3.5vw,3rem)] font-bold leading-tight">{line?.name ?? (tree.isPending ? "…" : "Linha não encontrada")}</h1>
          <p className="text-[clamp(1rem,1.5vw,1.5rem)] text-fg-muted">
            {ov?.current_shift_name ? `${ov.current_shift_name} · OEE do turno` : "OEE de hoje"}
          </p>
        </div>
        <div className="flex items-center gap-4">
          <span className={clsx("flex items-center gap-2 text-caption", ws === "connected" ? "text-ok" : "text-warn")}>
            <span className={clsx("size-2.5 rounded-full", ws === "connected" ? "animate-pulse bg-ok-solid" : "bg-warn-solid")} aria-hidden="true" />
            {ws === "connected" ? "Ao vivo" : "Reconectando…"}
          </span>
          <span className="text-[clamp(1.75rem,3vw,3rem)] font-semibold tabular">{clock}</span>
          <button type="button" onClick={() => void document.documentElement.requestFullscreen?.().catch(() => undefined)}
            className="min-h-10 rounded-lg border border-line px-3 text-caption text-fg-muted hover:bg-surface-2">Tela cheia</button>
          <Link to="/dashboard" className="inline-flex min-h-10 items-center rounded-lg border border-line px-3 text-caption text-fg-muted hover:bg-surface-2">Sair</Link>
        </div>
      </header>

      {current && (
        <section aria-live="polite" className="mb-6 flex flex-wrap items-center justify-between gap-4 rounded-2xl border-2 border-danger-solid bg-danger-solid/15 px-6 py-4">
          <div className="min-w-0">
            <p className="text-[clamp(1rem,1.6vw,1.5rem)] font-semibold uppercase tracking-wide text-danger">Parada em andamento</p>
            <p className="truncate text-[clamp(1.5rem,2.5vw,2.25rem)] font-bold">
              {current.machine_name ?? line?.machines.find((m) => m.id === current.machine_id)?.name ?? "Máquina"}
              <span className="ml-3 font-normal text-fg-muted">
                {current.reason_code && current.reason_code !== "UNK-01"
                  ? reasonLabel(current.reason_code, reasons.data)
                  : current.suggested_reason_code ? `${reasonLabel(current.suggested_reason_code, reasons.data)} (sugestão)` : "Sem motivo informado"}
              </span>
            </p>
          </div>
          <p className="text-[clamp(2.5rem,6vw,5rem)] font-bold leading-none tabular text-danger" data-testid="tv-timer">
            {formatTimer((now - Date.parse(current.start)) / 1000)}
          </p>
        </section>
      )}

      <div className="grid gap-6 lg:grid-cols-[auto_1fr]">
        <section aria-label="OEE" className="flex min-w-0 flex-col items-center justify-center gap-6 rounded-2xl border border-line bg-surface p-6">
          <KpiRing value={oee} label="OEE" size={320} stroke={26} valueClassName="text-[clamp(2.75rem,9vw,5.5rem)]" />
          <ApqBreakdown className="w-full max-w-md" size="lg" availability={ov?.availability} performance={ov?.performance} quality={ov?.quality} />
        </section>
        <div className="grid min-w-0 content-start gap-6">
          <section aria-label="Produção" className="grid grid-cols-3 gap-4">
            {[
              { label: "Cadência", value: ov?.cadence == null ? "—" : formatDec1(ov.cadence), unit: "peças/h" },
              { label: "Boas", value: formatInt(ov?.good), unit: "hoje" },
              { label: "Refugo", value: formatInt(ov?.scrap), unit: "hoje", warn: (ov?.scrap ?? 0) > 0 },
            ].map((k) => (
              <div key={k.label} className="rounded-2xl border border-line bg-surface p-5">
                <p className="text-[clamp(1rem,1.4vw,1.375rem)] text-fg-muted">{k.label}</p>
                <p className={clsx("text-[clamp(2.25rem,4.5vw,4rem)] font-bold leading-tight tabular", k.warn && "text-warn")}>{k.value}</p>
                <p className="text-caption text-fg-subtle">{k.unit}</p>
              </div>
            ))}
          </section>
          <section aria-label="Máquinas">
            <ul className="grid grid-cols-2 gap-4 xl:grid-cols-3">
              {(line?.machines ?? []).map((m) => {
                const stop = stopFor.get(m.id);
                return (
                  <li key={m.id} data-status={m.status} className={clsx("rounded-2xl border-2 p-5", STATUS_TILE[m.status] ?? STATUS_TILE.unknown)}>
                    <p className="truncate text-[clamp(1.125rem,1.8vw,1.75rem)] font-semibold text-fg">{m.name}</p>
                    <p className="text-[clamp(1.25rem,2vw,2rem)] font-bold">{statusLabel(m.status)}</p>
                    {stop && <p className="text-[clamp(1rem,1.5vw,1.5rem)] tabular">{formatTimer((now - Date.parse(stop.start)) / 1000)}</p>}
                  </li>
                );
              })}
            </ul>
          </section>
          {ov && ov.unjustified_count > 0 && (
            <p className="rounded-2xl border border-warn-solid/50 bg-warn-solid/10 px-5 py-3 text-[clamp(1rem,1.5vw,1.5rem)] text-warn">
              {ov.unjustified_count} parada(s) aguardando justificativa
            </p>
          )}
        </div>
      </div>
      {overview.isError && (
        <p role="alert" className="mt-4 text-danger">
          {overview.data ? "Sem conexão com o servidor — exibindo os últimos dados recebidos." : "Não foi possível carregar o OEE da linha."}
        </p>
      )}
      </div>
    </main>
  );
}
