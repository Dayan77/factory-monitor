import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import ErrorState from "@/components/ui/ErrorState";
import { Field, inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { queryKeys } from "@/hooks/useAssets";
import { gatewaysApi, type EnrollToken, type Gateway } from "@/services/gatewaysApi";
import { deviceCount, lineTemplatesApi, type ApplyLineTemplateResult, type LineTemplate } from "@/services/lineTemplatesApi";
import type { TreePlant } from "@/services/types";
import { EnrollmentInfo } from "@/pages/settings/GatewaysPage";
import { apiErrorMessage } from "@/utils/errors";
import { formatInt } from "@/utils/format";
import { baseUrlError } from "@/utils/onboarding";

export const DEFAULT_LINE_NAME = "Linha LED Driver";
const NEW_GW = "__new__";

type Applied = { result: ApplyLineTemplateResult; lineName: string; template: LineTemplate; gateway: Gateway | null };

/**
 * "Usar um modelo de linha" (onboarding step 2, C3/C4): picks a server template (e.g. ueaPowerDriver M1 + M2), the box
 * URLs and an optional gateway, and applies it — line, machines, positions and tags created (or updated, idempotent);
 * with a gateway, its ueapd devices and the tag assignment too.
 */
export default function LineTemplatePanel({ plant }: { plant: TreePlant }) {
  const qc = useQueryClient();
  const templates = useQuery({ queryKey: ["line-templates"], queryFn: () => lineTemplatesApi.list().then((r) => r.data), staleTime: 5 * 60_000 });
  const gws = useQuery({ queryKey: ["gateways", plant.id], queryFn: () => gatewaysApi.list(plant.id).then((r) => r.data) });

  const [key, setKey] = useState("");
  const tpl = useMemo(() => (templates.data ?? []).find((t) => t.key === key) ?? templates.data?.[0] ?? null, [templates.data, key]);
  const [lineName, setLineName] = useState(DEFAULT_LINE_NAME);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [gwChoice, setGwChoice] = useState("");
  const [newGwName, setNewGwName] = useState("Gateway da linha");
  const [touched, setTouched] = useState(false);
  const [nameEdited, setNameEdited] = useState(false);
  const [applied, setApplied] = useState<Applied | null>(null);
  const [createdGw, setCreatedGw] = useState<Gateway | null>(null);
  const [failedAt, setFailedAt] = useState<"gateway" | "apply">("apply");

  // URLs start at the template defaults (kept when the user switches back and forth between templates)
  useEffect(() => {
    if (!tpl) return;
    if (!nameEdited) setLineName(tpl.line_name || DEFAULT_LINE_NAME);
    setUrls((u) => {
      const next = { ...u };
      for (const d of tpl.devices) if (next[d.id] === undefined) next[d.id] = d.default_base_url;
      return next;
    });
  }, [tpl]); // eslint-disable-line react-hooks/exhaustive-deps

  const withGateway = gwChoice !== "";
  const urlErrors = useMemo(() => {
    const out: Record<string, string> = {};
    if (!withGateway) return out; // the URLs only go to a gateway
    for (const d of tpl?.devices ?? []) {
      const e = baseUrlError(urls[d.id] ?? d.default_base_url);
      if (e) out[d.id] = e;
    }
    return out;
  }, [tpl, urls, withGateway]);
  const nameError = lineName.trim() ? null : "informe o nome da linha";
  const gwNameError = gwChoice === NEW_GW && !newGwName.trim() ? "informe o nome do gateway" : null;
  const valid = !!tpl && !nameError && !gwNameError && Object.keys(urlErrors).length === 0;

  const apply = useMutation({
    mutationFn: async (): Promise<Applied> => {
      const t = tpl as LineTemplate;
      let gateway: Gateway | null = (gws.data ?? []).find((g) => g.id === gwChoice) ?? (createdGw?.id === gwChoice ? createdGw : null);
      if (gwChoice === NEW_GW) {
        try {
          gateway = (await gatewaysApi.create({ name: newGwName.trim(), plant_id: plant.id })).data;
        } catch (e) {
          setFailedAt("gateway");
          throw e;
        }
        // a retry after a failed apply reuses this gateway instead of creating another one
        setCreatedGw(gateway);
        setGwChoice(gateway.id);
      }
      setFailedAt("apply");
      // the box URLs are written into the gateway's devices: without a gateway there is nowhere to keep them
      const devices = gateway ? Object.fromEntries(t.devices.map((d) => [d.id, { base_url: (urls[d.id] ?? d.default_base_url).trim() }])) : {};
      const r = await lineTemplatesApi.apply(t.key, { plant_id: plant.id, line_name: lineName.trim(), gateway_id: gateway?.id ?? null, devices });
      return { result: r.data, lineName: r.data.line_name ?? lineName.trim(), template: t, gateway };
    },
    onSuccess: (a) => {
      setApplied(a);
      setTouched(false);
      void qc.invalidateQueries({ queryKey: queryKeys.tree });
      void qc.invalidateQueries({ queryKey: ["lines"] });
      void qc.invalidateQueries({ queryKey: ["machines"] });
      void qc.invalidateQueries({ queryKey: ["tags"] });
      void qc.invalidateQueries({ queryKey: ["onboarding-readiness"] });
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: ["gateways"] }),
  });

  const submit = () => {
    setTouched(true);
    if (valid) { setApplied(null); apply.mutate(); }
  };

  return (
    <section className="space-y-4 rounded-xl border border-accent/40 bg-accent/5 p-4" aria-labelledby="ob-tpl-title" data-testid="line-template">
      <div>
        <h3 id="ob-tpl-title" className="text-heading text-fg">Usar um modelo de linha</h3>
        <p className="text-caption text-fg-muted">Cria a linha, as máquinas, as posições e as tags prontas para o equipamento. Aplicar de novo atualiza o que já existe, sem duplicar.</p>
      </div>

      {templates.isPending ? (
        <LoadingRegion label="Carregando modelos…"><Skeleton className="h-28 rounded-xl" /></LoadingRegion>
      ) : templates.isError ? (
        <ErrorState title="Não foi possível carregar os modelos de linha" error={templates.error} onRetry={() => void templates.refetch()} />
      ) : !tpl ? (
        <p className="text-caption text-fg-subtle" data-testid="tpl-empty">Nenhum modelo de linha disponível no servidor. Cadastre pelo formulário ou pelo CSV abaixo.</p>
      ) : (
        <>
          <fieldset className="space-y-2">
            <legend className="mb-1 text-caption font-medium text-fg-muted">Modelo</legend>
            <div className={clsx("grid gap-2", (templates.data ?? []).length > 1 && "md:grid-cols-2")}>
              {(templates.data ?? []).map((t) => (
                <label key={t.key} className={clsx("flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border bg-surface p-3 transition-colors",
                  t.key === tpl.key ? "border-accent ring-2 ring-accent/30" : "border-line hover:border-line-strong")}>
                  <input type="radio" name="ob-tpl" value={t.key} checked={t.key === tpl.key} onChange={() => setKey(t.key)} className="mt-1 size-5 accent-[rgb(var(--c-accent))]" />
                  <span className="min-w-0">
                    <span className="block font-medium text-fg">{t.name}</span>
                    {t.description && <span className="block text-caption text-fg-subtle">{t.description}</span>}
                    <span className="mt-1 flex flex-wrap gap-1">{t.machines.map((m) => <Badge key={m} tone="neutral">{m}</Badge>)}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Nome da linha" htmlFor="ob-tpl-line" error={touched ? nameError : undefined}>
              <input id="ob-tpl-line" value={lineName} onChange={(e) => { setLineName(e.target.value); setNameEdited(true); }} className={inputClass} autoComplete="off" />
            </Field>
            <Field label="Gateway que lê os boxes (opcional)" htmlFor="ob-tpl-gw"
              hint={gwChoice ? "Os dispositivos e as tags vão para este gateway: a configuração chega sozinha a ele." : "Sem gateway: só o cadastro; atribua as tags depois em Gateways."}>
              <select id="ob-tpl-gw" value={gwChoice} onChange={(e) => setGwChoice(e.target.value)} className={inputClass} disabled={gws.isPending}>
                <option value="">Sem gateway (atribuir depois)</option>
                {(gws.data ?? []).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                {createdGw && !(gws.data ?? []).some((g) => g.id === createdGw.id) && <option value={createdGw.id}>{createdGw.name}</option>}
                <option value={NEW_GW}>+ Criar gateway agora</option>
              </select>
            </Field>
            {gws.isError && <p role="alert" className="text-caption text-danger sm:col-span-2">{apiErrorMessage(gws.error, "Não foi possível carregar os gateways da planta.")}</p>}
            {gwChoice === NEW_GW && (
              <Field label="Nome do novo gateway" htmlFor="ob-tpl-gw-name" error={touched ? gwNameError : undefined} className="sm:col-span-2">
                <input id="ob-tpl-gw-name" value={newGwName} onChange={(e) => setNewGwName(e.target.value)} className={inputClass} autoComplete="off" />
              </Field>
            )}
          </div>

          <fieldset className="space-y-2">
            <legend className="mb-1 text-caption font-medium text-fg-muted">Endereço dos boxes na rede da fábrica</legend>
            {!withGateway && <p className="text-caption text-warn" data-testid="tpl-urls-need-gw">As URLs ficam gravadas no gateway que lê os boxes: escolha ou crie um gateway acima para usá-las.</p>}
            <div className="grid gap-4 xl:grid-cols-2">
              {tpl.devices.map((d) => {
                const v = urls[d.id] ?? d.default_base_url;
                const err = urlErrors[d.id];
                return (
                  <Field key={d.id} label={`${d.label} — URL`} htmlFor={`ob-tpl-url-${d.id}`} error={err && (touched || v !== d.default_base_url) ? err : undefined}
                    hint={`Ex.: ${d.default_base_url} (dispositivo ${d.id})`}>
                    <input id={`ob-tpl-url-${d.id}`} value={v} disabled={!withGateway} inputMode="url" autoComplete="off" spellCheck={false} aria-invalid={!!err}
                      onChange={(e) => setUrls((u) => ({ ...u, [d.id]: e.target.value }))} className={clsx(inputClass, "font-mono")} />
                  </Field>
                );
              })}
            </div>
          </fieldset>

          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" onClick={submit} loading={apply.isPending} disabled={touched && !valid} data-testid="tpl-apply">
              {applied ? "Aplicar de novo" : "Aplicar modelo"}
            </Button>
            <span className="text-caption text-fg-subtle">{tpl.machines.length} máquina(s){tpl.tag_count != null ? ` · ${tpl.tag_count} tag(s)` : ""} · {tpl.devices.length} box(es)</span>
          </div>

          {apply.isError && (
            <p role="alert" className="rounded-lg border border-danger-solid/40 bg-danger-solid/10 p-3 text-caption text-danger" data-testid="tpl-error">
              {failedAt === "gateway"
                ? apiErrorMessage(apply.error, "Não foi possível criar o gateway.")
                : apiErrorMessage(apply.error, "Não foi possível aplicar o modelo de linha.")}
            </p>
          )}
          {applied && <AppliedResult a={applied} gateways={gws.data} />}
        </>
      )}
    </section>
  );
}

function AppliedResult({ a, gateways }: { a: Applied; gateways: Gateway[] | undefined }) {
  const r = a.result;
  const gw = r.gateway ? (gateways ?? []).find((g) => g.id === r.gateway?.id) ?? a.gateway : null;
  const [token, setToken] = useState<EnrollToken | null>(null);
  const enroll = useMutation({
    mutationFn: (id: string) => gatewaysApi.enrollToken(id).then((x) => x.data),
    onSuccess: setToken,
  });
  return (
    <div className="space-y-3 rounded-lg border border-ok-solid/40 bg-ok-solid/10 p-3 text-body text-fg" data-testid="tpl-result">
      <p role="status" className="font-semibold text-ok">Modelo aplicado: {a.lineName}{r.line_created === true ? " (linha criada)" : r.line_created === false ? " (linha existente atualizada)" : ""}</p>
      <ul className="space-y-1 text-caption text-fg-muted">
        <li>Máquinas: {r.machines.length > 0 ? r.machines.map((m) => `${m.name}${m.created === true ? " (nova)" : ""}`).join(", ") : "nenhuma"}</li>
        <li>Tags: {formatInt(r.tags_created)} criada(s), {formatInt(r.tags_updated)} atualizada(s){r.tags_unchanged != null ? `, ${formatInt(r.tags_unchanged)} sem mudança` : ""}{r.tags_skipped ? `, ${formatInt(r.tags_skipped)} ignorada(s) (outro protocolo/gateway)` : ""}</li>
        {r.gateway
          ? <li>Gateway{gw ? ` ${gw.name}` : ""} — {formatInt(deviceCount(r.gateway.devices))} dispositivo(s) configurado(s), {formatInt(r.gateway.tags_assigned)} tag(s) atribuída(s){r.gateway.config_version != null ? ` · configuração v${r.gateway.config_version}` : ""}</li>
          : <li>Sem gateway: atribua as tags a um gateway em Configurações → Gateways.</li>}
      </ul>
      {r.warnings.length > 0 && (
        <ul className="rounded-lg border border-warn-solid/40 bg-warn-solid/10 p-2 text-caption text-warn" data-testid="tpl-warnings">
          {r.warnings.map((w) => <li key={w}>Aviso: {w}</li>)}
        </ul>
      )}
      {gw && !gw.enrolled && !token && (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="primary" loading={enroll.isPending} onClick={() => enroll.mutate(gw.id)}>Gerar código de instalação do gateway</Button>
          <span className="text-caption text-fg-subtle">Um comando no computador da fábrica e o gateway se cadastra e recebe esta configuração.</span>
        </div>
      )}
      {enroll.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(enroll.error, "Não foi possível gerar o código.")}</p>}
      {gw && token && <EnrollmentInfo gw={gw} token={token} />}
    </div>
  );
}
