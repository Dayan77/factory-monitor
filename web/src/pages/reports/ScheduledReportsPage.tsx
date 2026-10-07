import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { usePlantTree } from "@/hooks/useAssets";
import { reportsApi } from "@/services/sprint6Api";
import type { CatalogEntry, ReportSchedule, ScopeType } from "@/services/sprint6Types";
import Button from "@/components/ui/Button";
import Badge from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import { Field, inputClass } from "@/components/ui/Field";
import Sheet from "@/components/ui/Sheet";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { useReportCatalog } from "@/components/reports/ExportMenu";
import { apiErrorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";
import {
  blobErrorMessage, cadenceLabel, DISPATCH_STATUS_LABEL, emptyScheduleForm, filenameFrom, FORMAT_LABEL, REPORT_LABEL, ROLE_LABEL,
  saveBlob, scheduleBody, validateSchedule, WEEKDAY_LABEL, type ScheduleForm,
} from "@/utils/reports";

const KEY = ["reports", "schedules"] as const;

function useScopeNames() {
  const tree = usePlantTree();
  return useMemo(() => {
    const m = new Map<string, string>();
    for (const p of tree.data ?? []) {
      m.set(p.id, p.name);
      for (const l of p.lines) m.set(l.id, `${l.name} · ${p.name}`);
    }
    return { names: m, tree: tree.data ?? [] };
  }, [tree.data]);
}

/** "Relatórios agendados" (reports.md §4–§6): create, pause, send now, version history. Manager / admin. */
export default function ScheduledReportsPage() {
  const qc = useQueryClient();
  const catalog = useReportCatalog();
  const schedules = useQuery({ queryKey: KEY, queryFn: () => reportsApi.schedules().then((r) => r.data) });
  const status = useQuery({ queryKey: ["reports", "status"], queryFn: () => reportsApi.status().then((r) => r.data), staleTime: 60_000 });
  const { names } = useScopeNames();
  const [creating, setCreating] = useState(false);
  const [history, setHistory] = useState<ReportSchedule | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const act = useMutation({
    mutationFn: async ({ s, op }: { s: ReportSchedule; op: "pause" | "resume" | "send" | "delete" }) => {
      if (op === "pause") return { op, r: (await reportsApi.pause(s.id)).data };
      if (op === "resume") return { op, r: (await reportsApi.resume(s.id)).data };
      if (op === "delete") { await reportsApi.deleteSchedule(s.id); return { op, r: null }; }
      return { op, r: (await reportsApi.sendNow(s.id)).data };
    },
    onSuccess: ({ op, r }) => {
      void qc.invalidateQueries({ queryKey: KEY });
      void qc.invalidateQueries({ queryKey: ["reports", "history"] });
      if (op === "send" && r && "status" in r) {
        const x = r as { status: string; period_label: string };
        setNotice({ tone: "ok", text: `Envio do período ${x.period_label}: ${DISPATCH_STATUS_LABEL[x.status] ?? x.status}.` });
      } else if (op === "delete") setNotice({ tone: "ok", text: "Agendamento excluído." });
    },
    onError: (e) => setNotice({ tone: "error", text: apiErrorMessage(e, "Não foi possível concluir a ação.") }),
  });

  return (
    <div className="mx-auto max-w-6xl space-y-4 animate-rise-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-title text-fg">Relatórios agendados</h2>
          <p className="text-caption text-fg-subtle">Gerados quando o período fecha (fim do turno pelo calendário + folga) e enviados por e-mail aos papéis escolhidos. Nunca antes de os dados fecharem.</p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)} data-testid="new-schedule" disabled={!catalog.data}>Novo agendamento</Button>
      </div>
      {status.data && !status.data.mail_configured && (
        <p role="status" data-testid="mail-warning" className="rounded-xl border border-[#b45309] bg-[#fef3c7] px-4 py-3 text-body text-[#78350f]">
          E-mail não configurado: os relatórios agendados são gerados e ficam só para download (histórico de cada agendamento).
        </p>
      )}
      {notice && <p role={notice.tone === "error" ? "alert" : "status"} className={clsx("rounded-xl px-4 py-3 text-body", notice.tone === "error" ? "bg-danger-solid/10 text-danger" : "bg-ok-solid/10 text-ok")}>{notice.text}</p>}

      {schedules.isPending ? <LoadingRegion><Skeleton className="h-40 rounded-xl" /></LoadingRegion>
        : schedules.isError ? <ErrorState error={schedules.error} onRetry={() => void schedules.refetch()} />
        : schedules.data.length === 0 ? (
          <Card><EmptyState title="Nenhum relatório agendado" description="Crie um agendamento para receber o relatório diário de produção, a revisão semanal de OEE ou o mensal da manutenção por e-mail."
            action={<Button variant="primary" onClick={() => setCreating(true)} disabled={!catalog.data}>Novo agendamento</Button>} /></Card>
        ) : (
          <ul className="space-y-3" data-testid="schedules">
            {schedules.data.map((s) => (
              <li key={s.id}>
                <Card className={clsx(!s.enabled && "opacity-80")} data-testid="schedule-card">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-heading text-fg">{s.name || s.report_name}</p>
                      <p className="text-caption text-fg-muted">
                        {REPORT_LABEL[s.report_type]} · {s.scope_type === "tenant" ? "todas as plantas" : names.get(s.scope_id ?? "") ?? "—"} · {s.formats.map((f) => FORMAT_LABEL[f]).join(" + ")}
                      </p>
                      <p className="text-caption text-fg-subtle">
                        {cadenceLabel(s)} · para {s.recipients.roles.map((r) => ROLE_LABEL[r] ?? r).join(", ") || "—"}
                        {s.next_send_at && s.enabled ? ` · próximo envio ${formatDateTime(s.next_send_at)}` : ""}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      {s.enabled ? <Badge tone="ok" dot>Ativo</Badge> : <Badge tone="warn">Pausado{s.paused_at ? ` desde ${formatDateTime(s.paused_at)}` : ""}</Badge>}
                      <Button size="sm" onClick={() => act.mutate({ s, op: s.enabled ? "pause" : "resume" })} disabled={act.isPending} data-testid={s.enabled ? "pause" : "resume"}>
                        {s.enabled ? "Pausar" : "Retomar"}
                      </Button>
                      <Button size="sm" onClick={() => act.mutate({ s, op: "send" })} loading={act.isPending && act.variables?.s.id === s.id && act.variables.op === "send"} data-testid="send-now">Enviar agora</Button>
                      <Button size="sm" variant="ghost" demoSafe onClick={() => setHistory(s)} data-testid="history">Histórico</Button>
                      <Button size="sm" variant="danger-ghost" onClick={() => { if (window.confirm("Excluir este agendamento? O histórico de versões continua disponível para download.")) act.mutate({ s, op: "delete" }); }}>Excluir</Button>
                    </div>
                  </div>
                </Card>
              </li>
            ))}
          </ul>
        )}

      {catalog.data && <CreateSheet open={creating} onClose={() => setCreating(false)} catalog={catalog.data} onCreated={() => { setCreating(false); setNotice({ tone: "ok", text: "Agendamento criado." }); }} />}
      <HistorySheet schedule={history} onClose={() => setHistory(null)} />
    </div>
  );
}

function CreateSheet({ open, onClose, catalog, onCreated }: { open: boolean; onClose: () => void; catalog: CatalogEntry[]; onCreated: () => void }) {
  const qc = useQueryClient();
  const { tree } = useScopeNames();
  const allowed = catalog.filter((c) => c.allowed);
  const [f, setF] = useState<ScheduleForm>(() => emptyScheduleForm(allowed[0]));
  const [touched, setTouched] = useState(false);
  const entry = allowed.find((c) => c.code === f.report_type);
  const errs = validateSchedule(f, entry);
  const save = useMutation({
    mutationFn: () => reportsApi.createSchedule(scheduleBody(f, entry!)),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: KEY }); setTouched(false); onCreated(); },
  });
  const scopeOptions = (t: ScopeType | "") => t === "plant" ? tree.map((p) => ({ id: p.id, name: p.name }))
    : t === "line" ? tree.flatMap((p) => p.lines.map((l) => ({ id: l.id, name: `${l.name} · ${p.name}` }))) : [];
  const roles = ["manager", "production_supervisor", "maintenance_planner", "admin", "technician", "operator"];
  return (
    <Sheet open={open} onClose={onClose} title="Novo agendamento" description="O período fecha pelo calendário da planta; o envio acontece no horário escolhido do dia seguinte."
      footer={
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancelar</Button>
          <Button variant="primary" loading={save.isPending} data-testid="save-schedule"
            onClick={() => { setTouched(true); if (Object.keys(errs).length === 0 && entry) save.mutate(); }}>Criar agendamento</Button>
        </div>
      }>
      <div className="space-y-4">
        <Field label="Relatório" htmlFor="sc-type" error={touched ? errs.report_type : undefined}>
          <select id="sc-type" value={f.report_type} className={inputClass}
            onChange={(e) => setF({ ...emptyScheduleForm(allowed.find((c) => c.code === e.target.value)), name: f.name })}>
            {allowed.map((c) => <option key={c.code} value={c.code}>{c.title}</option>)}
          </select>
        </Field>
        {entry && <p className="-mt-2 text-caption text-fg-subtle">“{entry.question}”</p>}
        <Field label="Nome (opcional)" htmlFor="sc-name"><input id="sc-name" value={f.name} maxLength={255} onChange={(e) => setF({ ...f, name: e.target.value })} className={inputClass} /></Field>
        {entry && entry.scope.length > 1 && (
          <Field label="Escopo" htmlFor="sc-scope" error={touched ? errs.scope_type : undefined}>
            <select id="sc-scope" value={f.scope_type} onChange={(e) => setF({ ...f, scope_type: e.target.value as ScopeType, scope_id: "" })} className={inputClass}>
              {entry.scope.map((s) => <option key={s} value={s}>{s === "tenant" ? "Todas as plantas" : s === "plant" ? "Planta" : "Linha"}</option>)}
            </select>
          </Field>
        )}
        {f.scope_type && f.scope_type !== "tenant" && (
          <Field label={f.scope_type === "line" ? "Linha" : "Planta"} htmlFor="sc-scope-id" error={touched ? errs.scope_id : undefined}>
            <select id="sc-scope-id" value={f.scope_id} onChange={(e) => setF({ ...f, scope_id: e.target.value })} className={inputClass} data-testid="sc-scope-id">
              <option value="">— escolha —</option>
              {scopeOptions(f.scope_type).map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
          </Field>
        )}
        {entry && (
          <fieldset>
            <legend className="text-caption font-medium text-fg-muted">Formatos</legend>
            <div className="mt-1 flex gap-4">
              {entry.formats.map((x) => (
                <label key={x} className="flex min-h-10 items-center gap-2 text-body text-fg">
                  <input type="checkbox" checked={f.formats.includes(x)} className="size-4 accent-[rgb(var(--c-accent))]"
                    onChange={(e) => setF({ ...f, formats: e.target.checked ? [...f.formats, x] : f.formats.filter((y) => y !== x) })} />
                  {FORMAT_LABEL[x]}
                </label>
              ))}
            </div>
            {touched && errs.formats && <p className="text-caption text-danger" role="alert">{errs.formats}</p>}
          </fieldset>
        )}
        {entry && entry.code !== "shift" && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Horário de envio (local)" htmlFor="sc-at" error={touched ? errs.deliver_at_local : undefined}>
              <input id="sc-at" type="time" value={f.deliver_at_local} onChange={(e) => setF({ ...f, deliver_at_local: e.target.value })} className={inputClass} />
            </Field>
            {entry.period === "week" && (
              <Field label="Dia da semana" htmlFor="sc-wd" error={touched ? errs.weekday : undefined}>
                <select id="sc-wd" value={f.weekday} onChange={(e) => setF({ ...f, weekday: e.target.value })} className={inputClass}>
                  {WEEKDAY_LABEL.map((w, i) => <option key={w} value={i}>{w}</option>)}
                </select>
              </Field>
            )}
            {entry.period === "month" && (
              <Field label="Dia do mês (1–28)" htmlFor="sc-md" error={touched ? errs.month_day : undefined}>
                <input id="sc-md" type="number" min={1} max={28} value={f.month_day} onChange={(e) => setF({ ...f, month_day: e.target.value })} className={inputClass} />
              </Field>
            )}
          </div>
        )}
        {entry?.code === "shift" && <p className="text-caption text-fg-subtle">Enviado ao fim de cada turno + 15 min (tempo para justificar as últimas paradas).</p>}
        <fieldset>
          <legend className="text-caption font-medium text-fg-muted">Destinatários (usuários do tenant por papel e área)</legend>
          <div className="mt-1 grid grid-cols-2 gap-1">
            {roles.map((r) => (
              <label key={r} className="flex min-h-10 items-center gap-2 text-body text-fg">
                <input type="checkbox" checked={f.roles.includes(r)} className="size-4 accent-[rgb(var(--c-accent))]"
                  onChange={(e) => setF({ ...f, roles: e.target.checked ? [...f.roles, r] : f.roles.filter((x) => x !== r) })} />
                {ROLE_LABEL[r] ?? r}
              </label>
            ))}
          </div>
          {touched && errs.roles && <p className="text-caption text-danger" role="alert">{errs.roles}</p>}
          <p className="mt-1 text-caption text-fg-subtle">Quem não tem acesso a horas/custos de OS recebe a versão sem essas seções.</p>
        </fieldset>
        {save.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(save.error, "Não foi possível criar o agendamento.")}</p>}
      </div>
    </Sheet>
  );
}

function HistorySheet({ schedule, onClose }: { schedule: ReportSchedule | null; onClose: () => void }) {
  const h = useQuery({
    queryKey: ["reports", "history", schedule?.id ?? ""],
    queryFn: () => reportsApi.history(schedule!.id).then((r) => r.data),
    enabled: !!schedule,
  });
  const [err, setErr] = useState("");
  async function download(id: string, fallback: string) {
    setErr("");
    try {
      const r = await reportsApi.download(id);
      saveBlob(r.data, filenameFrom(r.headers["content-disposition"] as string | undefined, fallback));
    } catch (e) {
      setErr(await blobErrorMessage(e, "Não foi possível baixar."));
    }
  }
  return (
    <Sheet open={!!schedule} onClose={onClose} size="lg" title={`Histórico — ${schedule?.name || schedule?.report_name || ""}`} description="Períodos enviados, versões geradas e entregas por e-mail.">
      {h.isPending ? <Skeleton className="h-40" /> : h.isError ? <ErrorState error={h.error} onRetry={() => void h.refetch()} /> : (
        <div className="space-y-5" data-testid="history-sheet">
          {err && <p role="alert" className="text-caption text-danger">{err}</p>}
          <section>
            <h3 className="mb-2 text-caption font-semibold uppercase tracking-wide text-fg-subtle">Versões</h3>
            {h.data.runs.length === 0 ? <p className="text-caption text-fg-subtle">Nenhuma versão gerada ainda.</p> : (
              <ul className="divide-y divide-line rounded-lg border border-line">
                {h.data.runs.map((r) => (
                  <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 p-3 text-body">
                    <span className="min-w-0">
                      <span className="block text-fg">{r.period_label ?? r.period_key} · v{r.version} · {FORMAT_LABEL[r.format] ?? r.format}</span>
                      <span className="block text-caption text-fg-subtle">gerado {formatDateTime(r.generated_at)} · dados até {formatDateTime(r.data_until)}{r.error ? ` · ${r.error}` : ""}</span>
                    </span>
                    {r.download_url ? <Button size="sm" demoSafe onClick={() => void download(r.id, r.filename ?? `relatorio.${r.format}`)}>Baixar</Button> : <Badge tone={r.status === "failed" ? "danger" : "neutral"}>{r.status}</Badge>}
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section>
            <h3 className="mb-2 text-caption font-semibold uppercase tracking-wide text-fg-subtle">Períodos</h3>
            {h.data.dispatches.length === 0 ? <p className="text-caption text-fg-subtle">Nenhum período processado.</p> : (
              <ul className="divide-y divide-line rounded-lg border border-line">
                {h.data.dispatches.map((d) => (
                  <li key={d.id} className="flex flex-wrap items-center justify-between gap-2 p-3 text-body">
                    <span className="text-fg">{d.period_label ?? d.period_key}{d.manual ? " (enviar agora)" : ""}</span>
                    <span className="text-caption text-fg-muted">{DISPATCH_STATUS_LABEL[d.status] ?? d.status} · {formatDateTime(d.send_at)}{d.attempts > 1 ? ` · ${d.attempts} tentativas` : ""}{d.last_error ? ` · ${d.last_error}` : ""}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section>
            <h3 className="mb-2 text-caption font-semibold uppercase tracking-wide text-fg-subtle">Entregas</h3>
            {h.data.deliveries.length === 0 ? <p className="text-caption text-fg-subtle">Nenhum e-mail enviado.</p> : (
              <ul className="divide-y divide-line rounded-lg border border-line">
                {h.data.deliveries.map((d) => (
                  <li key={d.id} className="flex flex-wrap items-center justify-between gap-2 p-3 text-caption">
                    <span className="text-fg">{d.email ?? "—"}</span>
                    <span className="text-fg-muted">{DISPATCH_STATUS_LABEL[d.status] ?? d.status}{d.link_only ? " · só link" : ""} · {formatDateTime(d.sent_at)}{d.error ? ` · ${d.error}` : ""}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </Sheet>
  );
}
