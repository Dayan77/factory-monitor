import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { reportsApi } from "@/services/sprint6Api";
import type { ReportCode, ReportFormat, ScopeType } from "@/services/sprint6Types";
import { useIsDemo } from "@/components/DemoBanner";
import { DEMO_WRITE_MSG } from "@/utils/demo";
import { blobErrorMessage, filenameFrom, FORMAT_LABEL, REPORT_LABEL, saveBlob } from "@/utils/reports";

export function useReportCatalog() {
  return useQuery({
    queryKey: ["reports", "catalog"],
    queryFn: () => reportsApi.catalog().then((r) => r.data),
    staleTime: 10 * 60_000,
  });
}

interface Props {
  report: ReportCode;
  scopeType?: ScopeType;
  scopeId?: string;
  /** Period params of the screen (productive_day, shift_id, week_start, month, cutoff_date, component_type). */
  params?: Record<string, string | undefined>;
  label?: string;
  /** Shift report: the supervisor's observations go into the report run (reports.md §1.2). */
  withObservations?: boolean;
  disabled?: boolean;
  className?: string;
}

/**
 * "Exportar PDF/Excel" (reports.md §6 item 7): generates the report of the same scope and period as the screen
 * (POST /api/reports/generate — a new version in report_runs) and downloads the file.
 */
export default function ExportMenu({ report, scopeType, scopeId, params = {}, label = "Exportar", withObservations, disabled, className }: Props) {
  const catalog = useReportCatalog();
  const demo = useIsDemo();
  const entry = catalog.data?.find((c) => c.code === report);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<ReportFormat | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [obs, setObs] = useState("");
  /** The menu opens towards the side with room (a button near the left edge must not open under the sidebar). */
  const [alignLeft, setAlignLeft] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  if (catalog.isSuccess && (!entry || !entry.allowed)) return null;     // no permission for this report: no button

  async function run(format: ReportFormat) {
    setBusy(format);
    setError("");
    setDone("");
    try {
      const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v)) as Record<string, string>;
      const res = await reportsApi.generate({
        code: report, format, params: clean,
        ...(scopeType ? { scope_type: scopeType } : {}), ...(scopeId ? { scope_id: scopeId } : {}),
        ...(withObservations && obs.trim() ? { observations: obs.trim() } : {}),
      });
      const name = filenameFrom(res.headers["content-disposition"] as string | undefined, `${report}.${format}`);
      saveBlob(res.data, name);
      const version = res.headers["x-report-version"];
      setDone(`${FORMAT_LABEL[format]} gerado${version ? ` (versão ${String(version)})` : ""}: ${name}`);
    } catch (err) {
      setError(await blobErrorMessage(err, "Não foi possível gerar o relatório."));
    } finally {
      setBusy(null);
    }
  }

  const blocked = demo || disabled;
  return (
    <div ref={box} className={clsx("relative inline-block print:hidden", className)}>
      <button type="button" aria-haspopup="menu" aria-expanded={open} aria-controls={menuId} data-testid={`export-${report}`}
        disabled={blocked || !entry} title={demo ? DEMO_WRITE_MSG : undefined}
        onClick={() => {
          const r = box.current?.getBoundingClientRect();
          setAlignLeft(!!r && r.right - 288 < (box.current?.closest("main")?.getBoundingClientRect().left ?? 0));
          setOpen((v) => !v);
        }}
        className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-line-strong bg-surface px-4 text-body font-medium text-fg hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-50">
        <svg viewBox="0 0 20 20" fill="currentColor" className="size-4" aria-hidden="true"><path d="M10.75 2.75a.75.75 0 0 0-1.5 0v8.614L6.295 8.235a.75.75 0 1 0-1.09 1.03l4.25 4.5a.75.75 0 0 0 1.09 0l4.25-4.5a.75.75 0 0 0-1.09-1.03l-2.955 3.129V2.75Z" /><path d="M3.5 12.75a.75.75 0 0 0-1.5 0v2.5A2.75 2.75 0 0 0 4.75 18h10.5A2.75 2.75 0 0 0 18 15.25v-2.5a.75.75 0 0 0-1.5 0v2.5c0 .69-.56 1.25-1.25 1.25H4.75c-.69 0-1.25-.56-1.25-1.25v-2.5Z" /></svg>
        {busy ? "Gerando…" : label}
      </button>
      {open && entry && (
        <div id={menuId} role="menu" aria-label={`Exportar ${REPORT_LABEL[report]}`}
          className={clsx("absolute z-40 mt-2 w-72 max-w-[calc(100vw-2rem)] rounded-xl border border-line bg-surface p-3 shadow-pop animate-fade-in", alignLeft ? "left-0" : "right-0")}>
          <p className="text-caption font-semibold text-fg">{entry.title}</p>
          <p className="mb-3 text-caption text-fg-subtle">Mesmo escopo e período da tela; gera uma nova versão.</p>
          {withObservations && (
            <label className="mb-3 block text-caption text-fg-muted">
              Observações do supervisor (opcional)
              <textarea value={obs} onChange={(e) => setObs(e.target.value)} maxLength={4000} rows={3}
                className="mt-1 w-full rounded-lg border border-line-strong bg-surface px-2 py-1.5 text-body text-fg focus:border-accent focus:outline-none" />
            </label>
          )}
          <div className="grid grid-cols-2 gap-2">
            {(["pdf", "xlsx"] as ReportFormat[]).map((f) => {
              const available = entry.formats.includes(f);
              return (
                <button key={f} type="button" role="menuitem" disabled={!available || !!busy} onClick={() => void run(f)}
                  data-testid={`export-${report}-${f}`}
                  title={available ? undefined : `${entry.name} não tem ${FORMAT_LABEL[f]}`}
                  className="flex min-h-11 items-center justify-center gap-2 rounded-lg border border-line-strong bg-surface-2/40 px-3 text-body font-semibold text-fg hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40">
                  {busy === f && <span aria-hidden="true" className="size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />}
                  {f === "pdf" ? "Exportar PDF" : "Exportar Excel"}
                </button>
              );
            })}
          </div>
          {error && <p role="alert" className="mt-2 text-caption text-danger">{error}</p>}
          {done && <p role="status" className="mt-2 break-all text-caption text-ok">{done}</p>}
        </div>
      )}
    </div>
  );
}
