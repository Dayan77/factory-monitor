/**
 * Predictive alert detail (predictive.md P-8/P-9/P-12): the server's explanation, the signal chart with the
 * baseline band, limits, EWMA/CUSUM and the RUL projection, the evidence, the history, and the lifecycle
 * actions the caller's roles allow (acknowledge, close with resolution, "Gerar OS preditiva").
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import clsx from "clsx";
import Sheet from "@/components/ui/Sheet";
import Button from "@/components/ui/Button";
import QueryView from "@/components/ui/QueryView";
import ErrorState from "@/components/ui/ErrorState";
import { Field, inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import ConditionChart from "./ConditionChart";
import { AlertStatePill, RulText, SeverityPill } from "./parts";
import { useAlert, useConvertAlert, useFeatures, useTransitionAlert } from "@/hooks/usePredictive";
import type { CloseResolution, PredictiveAlert } from "@/services/predictiveTypes";
import {
  actionLabel, CLOSE_RESOLUTIONS, evidenceText, FEATURE_LABEL, formatSignal, RESOLUTION_LABEL, SEVERITY_LABEL,
} from "@/utils/predictive";
import { formatDateTime } from "@/utils/format";
import { WO_STATE_LABEL } from "@/utils/maintenance";
import type { WoState } from "@/services/maintenanceTypes";
import { apiErrorMessage } from "@/utils/errors";

type Mode = "view" | "close" | "convert";

function ActionPanel({ a, onNotice }: { a: PredictiveAlert; onNotice: (t: string) => void }) {
  const [mode, setMode] = useState<Mode>("view");
  const [resolution, setResolution] = useState<CloseResolution | "">("");
  const [comment, setComment] = useState("");
  const [hours, setHours] = useState("");
  const [error, setError] = useState("");
  const transition = useTransitionAlert();
  const convert = useConvertAlert();
  const allowed = a.user_allowed_transitions ?? [];
  if (a.state === "closed" || a.state === "converted") return null;

  async function ack() {
    setError("");
    try {
      await transition.mutateAsync({ id: a.id, body: { to: "acknowledged", comment: comment.trim() || null } });
      onNotice("Alerta reconhecido.");
    } catch (e) { setError(apiErrorMessage(e, "Não foi possível reconhecer o alerta.")); }
  }
  async function close() {
    setError("");
    if (!resolution) { setError("Escolha a resolução."); return; }
    if (resolution === "rebaseline" && comment.trim().length < 3) { setError("Informe o motivo do novo normal."); return; }
    try {
      await transition.mutateAsync({ id: a.id, body: { to: "closed", resolution, comment: comment.trim() || null } });
      setMode("view");
      onNotice(`Alerta fechado: ${RESOLUTION_LABEL[resolution]}.`);
    } catch (e) { setError(apiErrorMessage(e, "Não foi possível fechar o alerta.")); }
  }
  async function doConvert() {
    setError("");
    const h = hours.trim() ? Number(hours.replace(",", ".")) : null;
    if (h != null && (!Number.isFinite(h) || h < 0)) { setError("Horas estimadas inválidas."); return; }
    try {
      const r = await convert.mutateAsync({ id: a.id, body: { estimated_hours: h, note: comment.trim() || null } });
      setMode("view");
      onNotice(`OS preditiva ${r.work_order?.number ?? ""} criada e aprovada.`.replace("  ", " "));
    } catch (e) { setError(apiErrorMessage(e, "Não foi possível gerar a OS preditiva.")); }
  }

  const canConvert = allowed.includes("converted");
  const canAck = allowed.includes("acknowledged");
  const canClose = allowed.includes("closed");
  if (!canConvert && !canAck && !canClose) {
    return <p className="text-caption text-fg-subtle">Seu perfil acompanha este alerta; reconhecer, fechar e gerar OS ficam com o planejador ou o gerente.</p>;
  }
  return (
    <div className="space-y-3" data-testid="alert-actions">
      {mode === "view" && (
        <div className="flex flex-wrap gap-2">
          {canConvert && <Button variant="primary" onClick={() => { setMode("convert"); setComment(""); }}>Gerar OS preditiva</Button>}
          {canAck && <Button onClick={() => void ack()} loading={transition.isPending}>Reconhecer</Button>}
          {canClose && <Button variant="ghost" onClick={() => { setMode("close"); setComment(""); }}>Fechar alerta…</Button>}
        </div>
      )}
      {mode === "convert" && (
        <div className="space-y-3 rounded-xl border border-line p-3">
          <p className="text-body text-fg">Cria <strong>uma</strong> OS do tipo preditiva, já aprovada, com o texto do alerta e o link do gráfico; prazo = data pessimista da RUL (sem RUL: hoje + 7 dias).</p>
          <Field label="Horas estimadas (opcional)" htmlFor="conv-hours" hint="Vazio = as horas do plano de condição da posição, se houver.">
            <input id="conv-hours" inputMode="decimal" value={hours} onChange={(e) => setHours(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Observação (opcional)" htmlFor="conv-note">
            <textarea id="conv-note" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} className={inputClass} />
          </Field>
          <div className="flex gap-2">
            <Button variant="primary" onClick={() => void doConvert()} loading={convert.isPending}>Gerar OS preditiva</Button>
            <Button variant="ghost" onClick={() => setMode("view")}>Voltar</Button>
          </div>
        </div>
      )}
      {mode === "close" && (
        <fieldset className="space-y-2 rounded-xl border border-line p-3">
          <legend className="px-1 text-body font-medium text-fg">Resolução</legend>
          {CLOSE_RESOLUTIONS.map((r) => (
            <label key={r.value} className={clsx("flex cursor-pointer gap-3 rounded-lg border p-2", resolution === r.value ? "border-accent bg-accent/10" : "border-line")}>
              <input type="radio" name="resolution" value={r.value} checked={resolution === r.value} onChange={() => setResolution(r.value)} className="mt-1" />
              <span><span className="block text-body font-medium text-fg">{r.label}</span><span className="block text-caption text-fg-subtle">{r.hint}</span></span>
            </label>
          ))}
          <Field label={resolution === "rebaseline" ? "Motivo (obrigatório)" : "Comentário (opcional)"} htmlFor="close-comment">
            <textarea id="close-comment" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} className={inputClass} />
          </Field>
          <div className="flex gap-2">
            <Button variant="primary" onClick={() => void close()} loading={transition.isPending}>Fechar alerta</Button>
            <Button variant="ghost" onClick={() => setMode("view")}>Voltar</Button>
          </div>
        </fieldset>
      )}
      {error && <p role="alert" className="text-body text-danger">{error}</p>}
    </div>
  );
}

function AlertBody({ a }: { a: PredictiveAlert }) {
  const [notice, setNotice] = useState("");
  const features = useFeatures({ position_id: a.position_id, feature: a.feature });
  const series = features.data?.series.find((s) => s.monitor_id === a.monitor_id) ?? features.data?.series[0];
  return (
    <div className="space-y-5" data-testid="alert-detail">
      <div className="flex flex-wrap items-center gap-2">
        <SeverityPill severity={a.severity} />
        <AlertStatePill state={a.state} />
        {a.resolution && <span className="text-caption text-fg-muted">{RESOLUTION_LABEL[a.resolution]}</span>}
        <span className="text-caption text-fg-subtle">aberto em {formatDateTime(a.opened_at)}</span>
      </div>

      {a.explanation ? (
        <p className="rounded-xl border-l-4 border-warn-solid bg-surface-2/60 px-4 py-3 text-body text-fg" data-testid="alert-explanation">{a.explanation}</p>
      ) : <p className="text-caption text-fg-subtle">O servidor não enviou o texto explicativo deste alerta.</p>}

      <p className="text-body"><RulText rul={a.rul_condition} /></p>

      {a.work_order_id && (
        <p className="text-body text-fg">
          OS preditiva: <Link className="font-semibold text-ok hover:underline" to={`/maintenance/work-orders?wo=${encodeURIComponent(a.work_order_id)}`}>{a.work_order_number ?? "abrir"}</Link>
          {a.work_order_state && <span className="text-fg-muted"> · {WO_STATE_LABEL[a.work_order_state as WoState] ?? a.work_order_state}</span>}
        </p>
      )}

      <p aria-live="polite" className={clsx("rounded-xl bg-ok-solid/15 px-4 py-3 text-body font-medium text-ok", !notice && "sr-only")}>{notice}</p>
      <ActionPanel a={a} onNotice={setNotice} />

      <section className="space-y-2">
        <h3 className="text-overline uppercase text-fg-subtle">{FEATURE_LABEL[a.feature]} · {a.position_name}</h3>
        {features.isPending ? <Skeleton className="h-64" /> : features.isError ? (
          <ErrorState title="Não foi possível carregar o gráfico do sinal" error={features.error} onRetry={() => void features.refetch()} />
        ) : series && series.points.length ? <ConditionChart series={series} rul={a.state === "closed" ? null : a.rul_condition} /> : (
          <p className="text-caption text-fg-subtle">Sem janelas para exibir.</p>
        )}
      </section>

      {a.triggers.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-overline uppercase text-fg-subtle">Evidências</h3>
          <ul className="divide-y divide-line rounded-xl border border-line" data-testid="alert-evidence">
            {a.triggers.map((t) => (
              <li key={t.source} className="flex flex-wrap items-baseline justify-between gap-2 px-3 py-2 text-body">
                <span className="font-medium text-fg">{evidenceText(t, a.unit)}</span>
                <span className="text-caption text-fg-subtle">
                  {t.severity ? `${SEVERITY_LABEL[t.severity]} · ` : ""}janelas {t.first_i ?? "?"}–{t.last_i ?? "?"} · desde {formatDateTime(t.first_at)}
                </span>
              </li>
            ))}
          </ul>
          {a.baseline?.m0 != null && (
            <p className="text-caption text-fg-subtle">Referência: {formatSignal(a.baseline.m0, a.unit)} ± {formatSignal(a.baseline.sigma, a.unit)} (σ robusto, {a.baseline.n ?? "?"} janelas, desde {formatDateTime(a.baseline.established_at)}).</p>
          )}
        </section>
      )}

      {a.history && a.history.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-overline uppercase text-fg-subtle">Histórico</h3>
          <ol className="space-y-1.5 text-caption">
            {a.history.map((h, i) => (
              <li key={`${h.at}-${i}`} className="text-fg-muted">
                <span className="font-medium text-fg">{actionLabel(h.action)}</span>
                {h.severity && h.action !== "close" ? ` (${SEVERITY_LABEL[h.severity]})` : ""}
                {" · "}{formatDateTime(h.at)}{h.by_name ? ` · ${h.by_name}` : h.source !== "user" ? " · automático" : ""}
                {h.resolution ? ` · ${RESOLUTION_LABEL[h.resolution]}` : ""}{h.comment ? ` — ${h.comment}` : ""}
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

export default function AlertSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useAlert(id);
  const a = q.data;
  return (
    <Sheet open={!!id} onClose={onClose} size="lg"
      title={a ? `${a.position_name ?? "Posição"} — ${FEATURE_LABEL[a.feature]}` : "Alerta preditivo"}
      description={a ? [a.machine_name, a.line_name].filter(Boolean).join(" · ") : undefined}>
      <QueryView query={q} errorTitle="Não foi possível carregar o alerta"
        loading={<LoadingRegion label="Carregando alerta…"><div className="space-y-3"><Skeleton className="h-6 w-48" /><Skeleton className="h-20" /><Skeleton className="h-64" /></div></LoadingRegion>}>
        {(d) => <AlertBody key={d.id} a={d} />}
      </QueryView>
    </Sheet>
  );
}
