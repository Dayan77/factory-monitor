import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import clsx from "clsx";
import { usePlantTree, queryKeys } from "@/hooks/useAssets";
import { useReasonCodes, useShifts } from "@/hooks/useEfficiency";
import { api, linesApi, shiftsApi } from "@/services/api";
import { gatewaysApi, type EnrollToken, type Gateway } from "@/services/gatewaysApi";
import { onboardingApi } from "@/services/sprint6Api";
import type { OnboardingProgress, OnboardingStep, Readiness, StepErrorDetail } from "@/services/sprint6Types";
import type { TreePlant } from "@/services/types";
import Button from "@/components/ui/Button";
import Badge from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import ErrorState from "@/components/ui/ErrorState";
import { Field, inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { Tabs } from "@/components/ui/Tabs";
import ImportPanel from "@/components/onboarding/ImportPanel";
import BrowsePanel from "@/components/onboarding/BrowsePanel";
import { EnrollmentInfo, GatewayStatusBadge } from "@/pages/settings/GatewaysPage";
import { apiErrorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";
import {
  canOpenStep, ianaZones, shiftTemplate, STEP_TITLES, UF_TIMEZONE, validateStep1, validateStep2, validateStep3,
} from "@/utils/onboarding";

const READINESS_LABEL: Record<string, string> = {
  timezone_confirmed: "Fuso da planta confirmado",
  output_machine_per_line: "Toda linha com máquina de saída",
  calendar: "Turnos ativos (ou operação 24 h)",
  ideal_cycle_time_output: "Máquina de saída com tempo de ciclo ideal",
  gateway_online: "Gateways online",
  run_state_or_cycles: "Toda máquina com estado (run) ou contador",
  part_counter_output: "Máquina de saída com contador de peças",
  data_flowing: "Dados chegando há 15 min contínuos",
  counter_max_set: "Contadores com valor máximo (rollover)",
  fault_code_map: "Mapa de códigos de falha",
  second_admin: "Mais de um administrador",
};

function useProgress() {
  return useQuery({ queryKey: ["onboarding"], queryFn: () => onboardingApi.get().then((r) => r.data) });
}

/** Errors / warnings of a 422 from PUT /api/onboarding. */
function stepError(err: unknown): StepErrorDetail | null {
  if (!isAxiosError(err) || err.response?.status !== 422) return null;
  const d = (err.response.data as { detail?: unknown } | undefined)?.detail;
  if (d && typeof d === "object" && "errors" in d) return d as StepErrorDetail;
  return null;
}

function StepShell({ n, title, intro, children, onDone, doneLabel = "Concluir passo", clientErrors = [], busy, serverError, warnings, done, canDone = true }: {
  n: number; title: string; intro: ReactNode; children: ReactNode; onDone: () => void; doneLabel?: string; clientErrors?: string[];
  busy?: boolean; serverError?: unknown; warnings?: string[]; done?: boolean; canDone?: boolean;
}) {
  const se = stepError(serverError);
  return (
    <Card>
      <CardHeader title={`${n}. ${title}`} description={intro} action={done ? <Badge tone="ok" dot>Concluído</Badge> : undefined} />
      <div className="space-y-5">{children}</div>
      <div className="mt-6 space-y-3 border-t border-line pt-4">
        {clientErrors.length > 0 && (
          <div role="alert" data-testid="step-errors" className="rounded-lg border border-danger-solid/40 bg-danger-solid/10 p-3 text-caption text-danger">
            <p className="font-semibold">Para concluir este passo:</p>
            <ul className="ml-4 list-disc">{clientErrors.map((e) => <li key={e}>{e}</li>)}</ul>
          </div>
        )}
        {se && (
          <div role="alert" data-testid="server-step-errors" className="rounded-lg border border-danger-solid/40 bg-danger-solid/10 p-3 text-caption text-danger">
            <p className="font-semibold">{se.message}</p>
            <ul className="ml-4 list-disc">{se.errors.map((e) => <li key={e}>{e}</li>)}</ul>
          </div>
        )}
        {!se && !!serverError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(serverError, "Não foi possível salvar o passo.")}</p>}
        {(warnings?.length ?? 0) > 0 && (
          <ul className="rounded-lg border border-warn-solid/40 bg-warn-solid/10 p-3 text-caption text-warn" data-testid="step-warnings">
            {warnings!.map((w) => <li key={w}>Aviso: {w}</li>)}
          </ul>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="primary" onClick={onDone} loading={busy} disabled={!canDone || clientErrors.length > 0} data-testid="step-done">{doneLabel}</Button>
        </div>
      </div>
    </Card>
  );
}

/** Self-service onboarding (docs/sprint6.md §4): 9 steps, resumable (progress on the server), validated. */
export default function OnboardingPage() {
  const { step: stepParam } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const progress = useProgress();
  const tree = usePlantTree();
  const p = progress.data;
  const plant = useMemo(() => (tree.data ?? []).find((x) => x.id === p?.plant_id) ?? null, [tree.data, p?.plant_id]);
  const step = Number(stepParam) || p?.current_step || 9;

  const put = useMutation({
    mutationFn: (a: { step: number; state: "pending" | "in_progress" | "done"; data?: Record<string, unknown> }) =>
      onboardingApi.put(a.step, a.state, a.data).then((r) => r.data),
    onSuccess: (data, a) => {
      qc.setQueryData(["onboarding"], data);
      void qc.invalidateQueries({ queryKey: queryKeys.tree });
      void qc.invalidateQueries({ queryKey: ["onboarding-readiness"] });
      if (a.state === "done" && a.step < 9) navigate(`/onboarding/${a.step + 1}`);
    },
  });
  useEffect(() => { put.reset(); }, [step]); // eslint-disable-line react-hooks/exhaustive-deps

  if (progress.isPending || tree.isPending) return <LoadingRegion><Skeleton className="h-96 rounded-xl" /></LoadingRegion>;
  if (progress.isError) return <ErrorState error={progress.error} onRetry={() => void progress.refetch()} />;
  if (!p) return null;
  if (!stepParam) return <Navigate to={`/onboarding/${p.current_step ?? 9}`} replace />;
  if (!canOpenStep(p.steps, step)) return <Navigate to={`/onboarding/${p.current_step ?? 1}`} replace />;

  const cur = p.steps.find((s) => s.step === step) as OnboardingStep;
  const done = (data?: Record<string, unknown>) => put.mutate({ step, state: "done", data });
  const common = { busy: put.isPending, serverError: put.error, warnings: put.data?.steps.find((s) => s.step === step)?.warnings ?? cur.warnings, done: cur.state === "done" };

  return (
    <div className="mx-auto max-w-6xl space-y-4 animate-rise-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-title text-fg">Assistente de implantação</h2>
          <p className="text-caption text-fg-subtle">Do zero até dados reais na tela. O progresso fica salvo: pode parar e continuar depois.</p>
        </div>
        {p.completed && <Badge tone="ok" dot>Pronto para produção{p.ready_at ? ` desde ${formatDateTime(p.ready_at)}` : ""}</Badge>}
      </div>
      <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
        <nav aria-label="Passos da implantação" className="min-w-0">
          <ol className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible" data-testid="wizard-steps">
            {p.steps.map((s) => {
              const open = canOpenStep(p.steps, s.step);
              const active = s.step === step;
              const inner = (
                <span className={clsx("flex min-h-11 items-center gap-2 rounded-lg border px-3 py-2 text-caption",
                  active ? "border-accent bg-accent/10 text-fg" : "border-line bg-surface text-fg-muted", !open && "opacity-50")}>
                  <span className={clsx("flex size-6 shrink-0 items-center justify-center rounded-full text-[11px] font-bold",
                    s.state === "done" ? "bg-[#15803d] text-white" : s.state === "in_progress" ? "bg-[#f59e0b] text-[#111827]" : "bg-surface-2 text-fg-muted")}>
                    {s.state === "done" ? "✓" : s.step}
                  </span>
                  <span className="whitespace-nowrap lg:whitespace-normal">{s.title || STEP_TITLES[s.step - 1]}</span>
                </span>
              );
              return (
                <li key={s.step} data-state={s.state} className="shrink-0">
                  {open ? <Link to={`/onboarding/${s.step}`} aria-current={active ? "step" : undefined}>{inner}</Link> : <span aria-disabled="true" title="Conclua os passos anteriores">{inner}</span>}
                </li>
              );
            })}
          </ol>
        </nav>
        <div className="min-w-0">
          {step === 1 && <Step1 progress={p} plant={plant} onDone={done} common={common} />}
          {step === 2 && <Step2 plant={plant} onDone={done} common={common} />}
          {step === 3 && <Step3 plant={plant} cur={cur} onDone={done} common={common} />}
          {step === 4 && <Step4 plantId={p.plant_id} onDone={done} common={common} />}
          {step === 5 && <Step5 plant={plant} onDone={done} common={common} />}
          {step === 6 && <Step6 plant={plant} onDone={done} common={common} />}
          {step === 7 && <Step7 plantId={p.plant_id} onDone={done} common={common} />}
          {step === 8 && <Step8 onDone={done} common={common} />}
          {step === 9 && <Step9 plantId={p.plant_id} onDone={done} common={common} completed={p.completed} />}
        </div>
      </div>
    </div>
  );
}

type Common = { busy: boolean; serverError: unknown; warnings: string[]; done: boolean };

function Step1({ progress, plant, onDone, common }: { progress: OnboardingProgress; plant: TreePlant | null; onDone: (d: Record<string, unknown>) => void; common: Common }) {
  const saved = progress.steps[0].data as { timezone?: string; address?: string; name?: string };
  const [f, setF] = useState({ name: plant?.name ?? saved.name ?? "", address: saved.address ?? "", uf: "", timezone: plant?.timezone ?? saved.timezone ?? "", confirmed: false });
  const zones = useMemo(() => ianaZones(), []);
  const errs = validateStep1(f, !!plant);
  const [touched, setTouched] = useState(false);
  return (
    <StepShell n={1} title="Empresa e planta" intro="Nome, endereço e fuso da planta. O fuso define turnos e dias produtivos: nunca usamos o do navegador sem você confirmar."
      onDone={() => { setTouched(true); if (Object.keys(errs).length === 0) onDone({ ...(plant ? { plant_id: plant.id } : { name: f.name.trim() }), address: f.address || undefined, timezone: f.timezone, timezone_confirmed: true }); }}
      clientErrors={touched ? Object.values(errs) : []} {...common}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Nome da planta" htmlFor="ob-name" error={touched ? errs.name : undefined}>
          <input id="ob-name" value={f.name} disabled={!!plant} onChange={(e) => setF({ ...f, name: e.target.value })} className={inputClass} placeholder="Ex.: Planta Manaus" />
        </Field>
        <Field label="Endereço" htmlFor="ob-address">
          <input id="ob-address" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} className={inputClass} />
        </Field>
        <Field label="Estado (UF) — sugere o fuso" htmlFor="ob-uf">
          <select id="ob-uf" value={f.uf} className={inputClass}
            onChange={(e) => setF({ ...f, uf: e.target.value, timezone: UF_TIMEZONE[e.target.value] ?? f.timezone, confirmed: false })}>
            <option value="">—</option>
            {Object.keys(UF_TIMEZONE).sort().map((uf) => <option key={uf} value={uf}>{uf}</option>)}
          </select>
        </Field>
        <Field label="Fuso da planta (IANA)" htmlFor="ob-tz" error={touched ? errs.timezone : undefined}>
          <input id="ob-tz" list="ob-tz-list" value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value, confirmed: false })} className={inputClass} placeholder="America/Sao_Paulo" />
          <datalist id="ob-tz-list">{zones.map((z) => <option key={z} value={z} />)}</datalist>
        </Field>
      </div>
      <label className="flex min-h-11 items-start gap-3 rounded-lg border border-line p-3 text-body text-fg">
        <input type="checkbox" checked={f.confirmed} onChange={(e) => setF({ ...f, confirmed: e.target.checked })} className="mt-1 size-5 accent-[rgb(var(--c-accent))]" data-testid="tz-confirm" />
        <span>Confirmo que a planta opera no fuso <strong>{f.timezone || "—"}</strong>{f.timezone && ` (agora são ${(() => { try { return new Date().toLocaleTimeString("pt-BR", { timeZone: f.timezone, hour: "2-digit", minute: "2-digit" }); } catch { return "—"; } })()} lá)`}.</span>
      </label>
    </StepShell>
  );
}

function Step2({ plant, onDone, common }: { plant: TreePlant | null; onDone: (d?: Record<string, unknown>) => void; common: Common }) {
  const qc = useQueryClient();
  const errs = validateStep2(plant);
  const [csvOpen, setCsvOpen] = useState(() => !plant || plant.lines.length === 0);
  const setOutput = useMutation({
    mutationFn: ({ lineId, machineId }: { lineId: string; machineId: string | null }) => linesApi.update(lineId, { output_machine_id: machineId }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: queryKeys.tree }),
  });
  return (
    <StepShell n={2} title="Linhas e máquinas" intro="Cadastre pelo formulário (Linhas / Máquinas) ou importe o CSV de ativos. Toda linha precisa da máquina de saída (a que conta as peças da linha)."
      onDone={() => onDone({ plant_id: plant?.id })} clientErrors={errs} {...common}>
      <div className="flex flex-wrap gap-2">
        <Link to="/lines" className="inline-flex min-h-10 items-center rounded-lg border border-line-strong px-4 text-body font-medium text-fg hover:bg-surface-2">Cadastrar linhas</Link>
        <Link to="/machines" className="inline-flex min-h-10 items-center rounded-lg border border-line-strong px-4 text-body font-medium text-fg hover:bg-surface-2">Cadastrar máquinas</Link>
      </div>
      {plant && plant.lines.length > 0 && (
        <ul className="divide-y divide-line rounded-lg border border-line" data-testid="ob-lines">
          {plant.lines.map((l) => (
            <li key={l.id} className="flex flex-wrap items-center justify-between gap-2 p-3">
              <div>
                <p className="font-medium text-fg">{l.name}</p>
                <p className="text-caption text-fg-subtle">{l.machines.length} máquina(s)</p>
              </div>
              <label className="flex items-center gap-2 text-caption text-fg-muted">
                Máquina de saída
                <select value={l.output_machine_id ?? ""} disabled={setOutput.isPending} className={clsx(inputClass, "w-auto")}
                  onChange={(e) => setOutput.mutate({ lineId: l.id, machineId: e.target.value || null })}>
                  <option value="">— escolha —</option>
                  {l.machines.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
              </label>
            </li>
          ))}
        </ul>
      )}
      {setOutput.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(setOutput.error, "Não foi possível definir a máquina de saída.")}</p>}
      <details className="rounded-lg border border-line p-3" open={csvOpen} onToggle={(e) => setCsvOpen((e.currentTarget as HTMLDetailsElement).open)}>
        <summary className="cursor-pointer text-body font-medium text-fg">Importar CSV de ativos</summary>
        <p className="my-2 text-caption text-fg-subtle">Colunas: linha; maquina; modelo; serie; saida (sim/não); posicao; tipo; fabricante; modelo_componente; serie_componente; vida_ciclos; vida_horas; vida_partidas; ciclos_por_peca.</p>
        <ImportPanel kind="assets" targetId={plant?.id ?? null} onConfirmed={() => void qc.invalidateQueries({ queryKey: queryKeys.tree })} />
      </details>
    </StepShell>
  );
}

function Step3({ plant, cur, onDone, common }: { plant: TreePlant | null; cur: OnboardingStep; onDone: (d?: Record<string, unknown>) => void; common: Common }) {
  const qc = useQueryClient();
  const shifts = useShifts(plant?.id);
  const [op24, setOp24] = useState(Boolean((cur.data as { operation_24h?: boolean }).operation_24h));
  const errs = shifts.data ? validateStep3(shifts.data, op24) : [];
  const create = useMutation({
    mutationFn: async (n: 1 | 2 | 3) => { for (const b of shiftTemplate(n, plant!.id)) await shiftsApi.create(b); },
    onSettled: () => void qc.invalidateQueries({ queryKey: ["shifts"] }),
  });
  return (
    <StepShell n={3} title="Turnos e calendário" intro="Escolha um modelo pronto ou personalize em Configurações → Turnos. Turnos não podem se sobrepor (D9)."
      onDone={() => onDone({ plant_id: plant?.id, operation_24h: op24 })} clientErrors={errs} {...common}>
      {shifts.isPending ? <Skeleton className="h-20" /> : (
        <>
          {(shifts.data ?? []).length > 0 ? (
            <ul className="divide-y divide-line rounded-lg border border-line" data-testid="ob-shifts">
              {(shifts.data ?? []).map((s) => (
                <li key={s.id} className="flex items-center justify-between p-3 text-body">
                  <span className="text-fg">{s.name}</span>
                  <span className="tabular text-fg-muted">{s.start_local}–{s.end_local} · {s.breaks.length} pausa(s){s.is_active ? "" : " · inativo"}</span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="grid gap-2 sm:grid-cols-3">
              {([1, 2, 3] as const).map((n) => (
                <Button key={n} onClick={() => create.mutate(n)} loading={create.isPending && create.variables === n} disabled={!plant}>
                  {n} turno{n > 1 ? "s" : ""} {n === 3 ? "(24 h, 7 dias)" : n === 2 ? "(seg–sáb)" : "(seg–sex)"}
                </Button>
              ))}
            </div>
          )}
          {create.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(create.error, "Não foi possível criar os turnos.")}</p>}
          <label className="flex items-center gap-2 text-body text-fg">
            <input type="checkbox" checked={op24} onChange={(e) => setOp24(e.target.checked)} className="size-5 accent-[rgb(var(--c-accent))]" />
            Operação 24 h sem turnos (D6) — só se a planta não tem turnos definidos
          </label>
          <Link to="/settings/production?tab=shifts" className="text-caption font-medium text-ok hover:underline">Personalizar turnos, pausas e feriados</Link>
        </>
      )}
    </StepShell>
  );
}

function useReadiness(plantId: string | null, refetch: number | false = false) {
  return useQuery({
    queryKey: ["onboarding-readiness", plantId],
    queryFn: () => onboardingApi.readiness(plantId).then((r) => r.data),
    enabled: !!plantId,
    refetchInterval: refetch,
  });
}

function CheckLine({ id, rd }: { id: string; rd: Readiness | undefined }) {
  const c = rd?.checks.find((x) => x.id === id);
  if (!c) return null;
  return (
    <p className={clsx("rounded-lg border p-3 text-caption", c.ok ? "border-ok-solid/40 text-ok" : c.required ? "border-danger-solid/40 text-danger" : "border-warn-solid/40 text-warn")}>
      {c.ok ? "✓" : c.required ? "✗" : "!"} {READINESS_LABEL[c.id] ?? c.id}: {c.detail}
    </p>
  );
}

function Step4({ plantId, onDone, common }: { plantId: string | null; onDone: (d?: Record<string, unknown>) => void; common: Common }) {
  const rd = useReadiness(plantId);
  return (
    <StepShell n={4} title="Produtos e tempo de ciclo ideal" intro="Cadastre os produtos, o tempo de ciclo ideal (PRI) por máquina e o produto padrão da máquina de saída. Sem PRI o OEE fica indefinido (aviso, não bloqueia)."
      onDone={() => onDone({ plant_id: plantId })} {...common}>
      <Link to="/settings/production?tab=products" className="inline-flex min-h-10 items-center rounded-lg border border-line-strong px-4 text-body font-medium text-fg hover:bg-surface-2">Abrir Produtos e ciclos</Link>
      <CheckLine id="ideal_cycle_time_output" rd={rd.data} />
    </StepShell>
  );
}

function Step5({ plant, onDone, common }: { plant: TreePlant | null; onDone: (d?: Record<string, unknown>) => void; common: Common }) {
  const qc = useQueryClient();
  const gws = useQuery({
    queryKey: ["gateways", plant?.id ?? ""],
    queryFn: () => gatewaysApi.list(plant!.id).then((r) => r.data),
    enabled: !!plant,
    refetchInterval: 5000,
  });
  const [name, setName] = useState("Gateway da planta");
  const [token, setToken] = useState<{ gw: Gateway; token: EnrollToken } | null>(null);
  const create = useMutation({
    mutationFn: async () => {
      const gw = (await gatewaysApi.create({ name: name.trim(), plant_id: plant!.id })).data;
      const tk = (await gatewaysApi.enrollToken(gw.id)).data;
      return { gw, token: tk };
    },
    onSuccess: (r) => { setToken(r); void qc.invalidateQueries({ queryKey: ["gateways"] }); },
  });
  const code = useMutation({
    mutationFn: async (gw: Gateway) => ({ gw, token: (await gatewaysApi.enrollToken(gw.id)).data }),
    onSuccess: setToken,
  });
  const online = (gws.data ?? []).some((g) => g.status === "online");
  return (
    <StepShell n={5} title="Conectar gateway" intro="Crie o gateway, instale-o no computador da fábrica com o código de cadastro e aguarde o primeiro heartbeat (o gateway só faz chamadas de saída)."
      onDone={() => onDone({ plant_id: plant?.id })} clientErrors={gws.data && !online ? ["aguardando um gateway online nesta planta"] : []} {...common}>
      {(gws.data ?? []).length > 0 && (
        <ul className="divide-y divide-line rounded-lg border border-line" data-testid="ob-gateways">
          {(gws.data ?? []).map((g) => (
            <li key={g.id} className="flex flex-wrap items-center justify-between gap-2 p-3">
              <span className="font-medium text-fg">{g.name}</span>
              <span className="flex items-center gap-2">
                <GatewayStatusBadge status={g.status} />
                {!g.enrolled && <Button size="sm" onClick={() => code.mutate(g)} loading={code.isPending}>Gerar código</Button>}
              </span>
            </li>
          ))}
        </ul>
      )}
      {(gws.data ?? []).length === 0 && (
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Nome do gateway" htmlFor="ob-gw" className="min-w-[16rem] flex-1">
            <input id="ob-gw" value={name} onChange={(e) => setName(e.target.value)} className={inputClass} />
          </Field>
          <Button variant="primary" onClick={() => create.mutate()} loading={create.isPending} disabled={!plant || !name.trim()} data-testid="ob-create-gw">Criar gateway e gerar código</Button>
        </div>
      )}
      {(create.isError || code.isError) && <p role="alert" className="text-caption text-danger">{apiErrorMessage(create.error ?? code.error, "Não foi possível criar o gateway.")}</p>}
      {token && <EnrollmentInfo gw={token.gw} token={token.token} />}
      {!online && (gws.data ?? []).length > 0 && <p className="text-caption text-fg-muted" role="status">Esperando o primeiro heartbeat… (atualiza a cada 5 s)</p>}
    </StepShell>
  );
}

function Step6({ plant, onDone, common }: { plant: TreePlant | null; onDone: (d?: Record<string, unknown>) => void; common: Common }) {
  const qc = useQueryClient();
  const gws = useQuery({ queryKey: ["gateways", plant?.id ?? ""], queryFn: () => gatewaysApi.list(plant!.id).then((r) => r.data), enabled: !!plant });
  const [gwId, setGwId] = useState("");
  const [mode, setMode] = useState<"csv" | "opcua">("csv");
  const gw = (gws.data ?? []).find((g) => g.id === gwId) ?? gws.data?.[0];
  return (
    <StepShell n={6} title="Importar tags" intro="CSV de tags (§4.1) ou navegação OPC UA pelo gateway (§4.2); atribua papel, máquina e posição. A validação usa as mesmas regras do parser do edge."
      onDone={() => onDone({ plant_id: plant?.id })} {...common}>
      {gws.isPending ? <Skeleton className="h-10" /> : !gw ? <p className="text-caption text-warn">Nenhum gateway nesta planta: volte ao passo 5.</p> : (
        <>
          <div className="flex flex-wrap items-center gap-3">
            {(gws.data ?? []).length > 1 && (
              <select aria-label="Gateway" value={gw.id} onChange={(e) => setGwId(e.target.value)} className={clsx(inputClass, "w-auto")}>
                {(gws.data ?? []).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
              </select>
            )}
            <span className="text-caption text-fg-muted">{gw.name} · {gw.tag_count} tag(s) atribuída(s)</span>
            <Tabs id="ob-tags-mode" label="Origem das tags" size="sm" value={mode} onChange={setMode}
              items={[{ value: "csv", label: "CSV de tags" }, { value: "opcua", label: "Navegar OPC UA" }]} />
          </div>
          {mode === "csv"
            ? <ImportPanel kind="tags" targetId={gw.id} onConfirmed={() => void qc.invalidateQueries({ queryKey: ["gateways"] })} />
            : plant && <BrowsePanel gateway={gw} plant={plant} onConfirmed={() => void qc.invalidateQueries({ queryKey: ["gateways"] })} />}
        </>
      )}
    </StepShell>
  );
}

function Step7({ plantId, onDone, common }: { plantId: string | null; onDone: (d?: Record<string, unknown>) => void; common: Common }) {
  const reasons = useReasonCodes();
  const rd = useReadiness(plantId);
  const counts = ["EQP-", "SET-", "MAT-", "PLN-"].map((pre) => ({ pre, n: (reasons.data ?? []).filter((r) => r.is_active && r.code.startsWith(pre)).length }));
  const missing = reasons.data ? counts.filter((c) => c.n === 0).map((c) => `catálogo sem os códigos padrão ${c.pre}*`) : [];
  return (
    <StepShell n={7} title="Motivos de parada e códigos de falha" intro="O catálogo padrão (EQP-*, SET-*, MAT-*, PLN-*…) já vem pronto; ajuste em Configurações → Motivos. O mapa de códigos de falha por máquina é opcional (sem ele: EQP-01)."
      onDone={() => onDone({ plant_id: plantId })} clientErrors={missing} {...common}>
      <ul className="grid gap-2 sm:grid-cols-4">
        {counts.map((c) => <li key={c.pre} className="rounded-lg border border-line p-3 text-center"><p className="text-title tabular text-fg">{c.n}</p><p className="text-caption text-fg-subtle">{c.pre}*</p></li>)}
      </ul>
      <CheckLine id="fault_code_map" rd={rd.data} />
      <Link to="/settings/production?tab=reasons" className="text-caption font-medium text-ok hover:underline">Ajustar motivos de parada</Link>
    </StepShell>
  );
}

const ROLES = [
  { value: "operator", label: "Operador" }, { value: "production_supervisor", label: "Supervisor de produção" },
  { value: "maintenance_planner", label: "Planejador de manutenção" }, { value: "technician", label: "Técnico" },
  { value: "manager", label: "Gestor" }, { value: "admin", label: "Administrador" },
];

function Step8({ onDone, common }: { onDone: (d?: Record<string, unknown>) => void; common: Common }) {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<{ id: string; name: string; email: string; role: string; roles?: string[] }[]>("/api/users/").then((r) => r.data) });
  const [f, setF] = useState({ name: "", email: "", role: "operator", password: "" });
  const add = useMutation({
    mutationFn: () => api.post("/api/users/", { name: f.name.trim(), email: f.email.trim(), role: f.role, password: f.password }),
    onSuccess: () => { setF({ name: "", email: "", role: "operator", password: "" }); void qc.invalidateQueries({ queryKey: ["users"] }); },
  });
  const admins = (users.data ?? []).filter((u) => u.role === "admin" || u.roles?.includes("admin")).length;
  return (
    <StepShell n={8} title="Usuários e papéis" intro="Convide operadores, supervisores, planejador e técnicos. Recomendado: mais de um administrador (aviso, não bloqueia)."
      onDone={() => onDone({})} warnings={admins <= 1 ? ["recomendado ao menos 1 admin além de quem implantou"] : common.warnings}
      busy={common.busy} serverError={common.serverError} done={common.done}>
      {users.isPending ? <Skeleton className="h-20" /> : users.isError ? <ErrorState error={users.error} /> : (
        <ul className="divide-y divide-line rounded-lg border border-line">
          {(users.data ?? []).map((u) => (
            <li key={u.id} className="flex flex-wrap items-center justify-between gap-2 p-3 text-body">
              <span className="text-fg">{u.name}</span>
              <span className="text-caption text-fg-subtle">{(u.roles?.length ? u.roles : [u.role]).map((r) => ROLES.find((x) => x.value === r)?.label ?? r).join(" · ")}</span>
            </li>
          ))}
        </ul>
      )}
      <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <Field label="Nome" htmlFor="ob-u-name"><input id="ob-u-name" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} className={inputClass} /></Field>
        <Field label="E-mail" htmlFor="ob-u-email"><input id="ob-u-email" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} className={inputClass} /></Field>
        <Field label="Papel" htmlFor="ob-u-role">
          <select id="ob-u-role" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })} className={inputClass}>
            {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </Field>
        <Field label="Senha inicial (a pessoa troca no 1º acesso)" htmlFor="ob-u-pass" hint="Mínimo 8 caracteres.">
          <input id="ob-u-pass" type="password" required minLength={8} autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} className={inputClass} />
        </Field>
        <div className="sm:col-span-2 flex flex-wrap items-center gap-2">
          <Button type="submit" loading={add.isPending}>Adicionar usuário</Button>
          {add.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(add.error, "Não foi possível adicionar.")}</p>}
        </div>
      </form>
    </StepShell>
  );
}

function Step9({ plantId, onDone, common, completed }: { plantId: string | null; onDone: (d?: Record<string, unknown>) => void; common: Common; completed: boolean }) {
  const rd = useReadiness(plantId, 15_000);
  const d = rd.data;
  const required = d?.checks.filter((c) => c.required) ?? [];
  const optional = d?.checks.filter((c) => !c.required) ?? [];
  return (
    <StepShell n={9} title="Verificação “pronto para produção”" intro={`Checklist automático, atualizado a cada 15 s. Todos os itens obrigatórios verdes com ${d?.window_min ?? 15} min de dados contínuos.`}
      onDone={() => onDone({ plant_id: plantId })} doneLabel={completed ? "Verificar de novo" : "Concluir implantação"} canDone={!!d?.ready}
      clientErrors={d && !d.ready ? ["itens obrigatórios ainda não estão verdes"] : []} {...common}>
      {rd.isPending ? <Skeleton className="h-64" /> : rd.isError ? <ErrorState error={rd.error} onRetry={() => void rd.refetch()} /> : d && (
        <>
          <p className="text-caption text-fg-subtle" role="status">Verificado em {formatDateTime(d.checked_at)}{d.ready ? " · tudo pronto" : ""}</p>
          {[{ t: "Obrigatórios", list: required }, { t: "Recomendações", list: optional }].map((g) => (
            <section key={g.t}>
              <h3 className="mb-2 text-caption font-semibold uppercase tracking-wide text-fg-subtle">{g.t}</h3>
              <ul className="space-y-2" data-testid="readiness">
                {g.list.map((c) => (
                  <li key={c.id} data-check={c.id} data-status={c.status}
                    className={clsx("flex flex-wrap items-start justify-between gap-2 rounded-lg border p-3",
                      c.status === "ok" ? "border-ok-solid/40" : c.status === "fail" ? "border-danger-solid/50 bg-danger-solid/5" : "border-warn-solid/50 bg-warn-solid/5")}>
                    <div className="flex min-w-0 items-start gap-2">
                      <span aria-hidden="true" className={clsx("mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold",
                        c.status === "ok" ? "bg-[#15803d] text-white" : c.status === "fail" ? "bg-[#b91c1c] text-white" : "bg-[#f59e0b] text-[#111827]")}>
                        {c.status === "ok" ? "✓" : c.status === "fail" ? "✗" : "!"}
                      </span>
                      <div className="min-w-0">
                        <p className="text-body font-medium text-fg">{READINESS_LABEL[c.id] ?? c.id}<span className="sr-only"> — {c.status === "ok" ? "ok" : c.status === "fail" ? "pendente" : "aviso"}</span></p>
                        <p className="text-caption text-fg-muted">{c.detail}</p>
                      </div>
                    </div>
                    {!c.ok && c.fix_link && <Link to={c.fix_link} className="shrink-0 text-caption font-medium text-ok hover:underline">Corrigir</Link>}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </>
      )}
    </StepShell>
  );
}
