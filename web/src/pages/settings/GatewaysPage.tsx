import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Card } from "@/components/ui/Card";
import Badge, { type Tone } from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import EmptyState from "@/components/ui/EmptyState";
import ErrorState from "@/components/ui/ErrorState";
import QueryView from "@/components/ui/QueryView";
import Sheet from "@/components/ui/Sheet";
import { CodeBlock } from "@/components/ui/CopyButton";
import { Field, inputClass } from "@/components/ui/Field";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { usePlantTree } from "@/hooks/useAssets";
import { useNow } from "@/hooks/useNow";
import { useAuthStore } from "@/store/auth";
import { tagsApi } from "@/services/api";
import {
  GATEWAY_STATUS_LABEL, gatewaysApi, installCommands, type EnrollToken, type Gateway, type GatewayDevice, type GatewayStatus,
} from "@/services/gatewaysApi";
import type { Tag, TreePlant } from "@/services/types";
import { can } from "@/utils/maintenance";
import { formatDateTime, formatInt } from "@/utils/format";
import { formatAgo, secondsSince } from "@/utils/time";
import { apiErrorMessage } from "@/utils/errors";
import { useUrlFilters } from "@/pages/maintenance/filters";

const STATUS_TONE: Record<GatewayStatus, Tone> = { online: "ok", degraded: "warn", offline: "danger", pending: "neutral" };
const gwKeys = { all: ["gateways"] as const, one: (id: string) => ["gateways", id] as const };

export function GatewayStatusBadge({ status }: { status: GatewayStatus }) {
  return <Badge tone={STATUS_TONE[status]} dot pulse={status === "online"}>{GATEWAY_STATUS_LABEL[status]}</Badge>;
}

function seen(iso: string | null, now: number) {
  const s = secondsSince(iso, now);
  return s == null ? "nunca" : formatAgo(s);
}

function plantName(tree: TreePlant[] | undefined, id: string) {
  return tree?.find((p) => p.id === id)?.name ?? "—";
}

function uptimeText(s: number | null | undefined) {
  if (s == null) return "—";
  const h = Math.floor(s / 3600);
  return h >= 24 ? `${Math.floor(h / 24)} d ${h % 24} h` : h > 0 ? `${h} h ${Math.floor((s % 3600) / 60)} min` : `${Math.floor(s / 60)} min`;
}

// ── enrollment ───────────────────────────────────────────────────

function EnrollmentInfo({ gw, token }: { gw: Gateway; token: EnrollToken }) {
  const user = useAuthStore((s) => s.user);
  const cmds = installCommands({
    origin: window.location.origin, gatewayId: gw.id, tenantId: user?.tenant_id ?? gw.tenant_id, code: token.code,
    mqttHost: window.location.hostname,
  });
  return (
    <div className="space-y-4" data-testid="enroll-info">
      <div className="rounded-xl border-2 border-accent/50 bg-accent/10 p-4 text-center">
        <p className="text-caption text-fg-muted">Código de cadastro (uso único)</p>
        <p className="font-mono text-display font-bold tracking-widest text-fg" data-testid="enroll-code">{token.code}</p>
        <p className="text-caption text-fg-subtle">Expira em {formatDateTime(token.expires_at)}. Um novo código substitui este.</p>
      </div>
      <CodeBlock label="1. No computador do gateway: trocar o código pelo token" code={cmds.enroll} testId="cmd-enroll" />
      <CodeBlock label="2. Subir o gateway com o gateway_id, o token e o usuário MQTT devolvidos" code={cmds.run} testId="cmd-run" />
      <p className="text-caption text-fg-subtle">O token e a senha MQTT aparecem só uma vez, na resposta do passo 1 — guarde-os no .env do gateway. A configuração (dispositivos e tags) chega sozinha pelo servidor e é recarregada quando muda.</p>
    </div>
  );
}

function AddGatewaySheet({ open, onClose, onOpen }: { open: boolean; onClose: () => void; onOpen: (id: string) => void }) {
  const tree = usePlantTree();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [plant, setPlant] = useState("");
  const [created, setCreated] = useState<{ gw: Gateway; token: EnrollToken } | null>(null);
  const [err, setErr] = useState("");
  const create = useMutation({
    mutationFn: async () => {
      const gw = (await gatewaysApi.create({ name: name.trim(), plant_id: plant })).data;
      const token = (await gatewaysApi.enrollToken(gw.id)).data;
      return { gw, token };
    },
    onSuccess: (r) => { setCreated(r); void qc.invalidateQueries({ queryKey: gwKeys.all }); },
    onError: (e) => setErr(apiErrorMessage(e, "Não foi possível criar o gateway.")),
  });
  useEffect(() => {
    if (open) { setName(""); setErr(""); setCreated(null); setPlant(tree.data?.[0]?.id ?? ""); }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!plant && tree.data?.[0]) setPlant(tree.data[0].id); }, [tree.data, plant]);

  return (
    <Sheet open={open} onClose={onClose} size="lg" title={created ? `Gateway ${created.gw.name} criado` : "Adicionar gateway"}
      description={created ? "Passo 2 de 2 — instale e cadastre o gateway na fábrica." : "Passo 1 de 2 — nome e planta."}
      footer={created ? (
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={() => { onClose(); onOpen(created.gw.id); }}>Atribuir tags e ver saúde</Button>
          <Button variant="ghost" onClick={onClose}>Fechar</Button>
        </div>
      ) : (
        <div className="flex gap-2">
          <Button variant="primary" disabled={!name.trim() || !plant} loading={create.isPending} onClick={() => { setErr(""); create.mutate(); }}>Criar e gerar código</Button>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
        </div>
      )}>
      {created ? <EnrollmentInfo gw={created.gw} token={created.token} /> : (
        <div className="space-y-4">
          <Field label="Nome" htmlFor="gw-name" hint="Ex.: Gateway Linha de Montagem (painel elétrico 2)">
            <input id="gw-name" value={name} onChange={(e) => setName(e.target.value)} className={inputClass} autoComplete="off" />
          </Field>
          <Field label="Planta" htmlFor="gw-plant">
            <select id="gw-plant" value={plant} onChange={(e) => setPlant(e.target.value)} className={inputClass}>
              {(tree.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
          <p className="text-caption text-fg-subtle">Depois de criar, você recebe um código de cadastro (24 h) e os comandos de instalação. Os dispositivos (CLP Modbus/OPC UA) e as tags podem ser configurados agora ou depois.</p>
          {err && <p role="alert" className="text-body text-danger">{err}</p>}
        </div>
      )}
    </Sheet>
  );
}

// ── detail ───────────────────────────────────────────────────────

function DevicesEditor({ gw }: { gw: Gateway }) {
  const qc = useQueryClient();
  const [devices, setDevices] = useState<GatewayDevice[]>(gw.devices);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const save = useMutation({
    mutationFn: () => gatewaysApi.update(gw.id, { devices }),
    onSuccess: () => { setMsg({ ok: true, text: "Dispositivos salvos." }); void qc.invalidateQueries({ queryKey: gwKeys.all }); },
    onError: (e) => setMsg({ ok: false, text: apiErrorMessage(e, "Não foi possível salvar.") }),
  });
  const upd = (i: number, patch: Partial<GatewayDevice>) => setDevices((d) => d.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  return (
    <div className="space-y-3" data-testid="gw-devices">
      {devices.length === 0 && <p className="text-caption text-fg-subtle">Nenhum dispositivo. Adicione o CLP que o gateway lê.</p>}
      {devices.map((d, i) => (
        <div key={i} className="grid gap-2 rounded-xl border border-line p-3 sm:grid-cols-[8rem_7rem_1fr_auto] sm:items-end">
          <Field label="Id" htmlFor={`dev-id-${i}`}><input id={`dev-id-${i}`} value={d.id} onChange={(e) => upd(i, { id: e.target.value })} className={inputClass} /></Field>
          <Field label="Protocolo" htmlFor={`dev-p-${i}`}>
            <select id={`dev-p-${i}`} value={d.protocol} onChange={(e) => upd(i, e.target.value === "opcua" ? { protocol: "opcua", host: undefined, port: undefined, endpoint: "opc.tcp://" } : { protocol: "modbus", endpoint: undefined, host: "", port: 502 })} className={inputClass}>
              <option value="modbus">Modbus TCP</option>
              <option value="opcua">OPC UA</option>
            </select>
          </Field>
          {d.protocol === "opcua" ? (
            <Field label="Endpoint (sem credencial)" htmlFor={`dev-ep-${i}`}><input id={`dev-ep-${i}`} value={d.endpoint ?? ""} onChange={(e) => upd(i, { endpoint: e.target.value })} className={inputClass} /></Field>
          ) : (
            <div className="grid grid-cols-[1fr_5rem] gap-2">
              <Field label="Host" htmlFor={`dev-h-${i}`}><input id={`dev-h-${i}`} value={d.host ?? ""} onChange={(e) => upd(i, { host: e.target.value })} className={inputClass} /></Field>
              <Field label="Porta" htmlFor={`dev-po-${i}`}><input id={`dev-po-${i}`} inputMode="numeric" value={d.port ?? ""} onChange={(e) => upd(i, { port: Number(e.target.value) || undefined })} className={inputClass} /></Field>
            </div>
          )}
          <Button variant="danger-ghost" aria-label={`Remover dispositivo ${d.id}`} onClick={() => setDevices((x) => x.filter((_, j) => j !== i))}>Remover</Button>
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => setDevices((d) => [...d, { id: `plc-${d.length + 1}`, protocol: "modbus", host: "", port: 502 }])}>Adicionar dispositivo</Button>
        <Button size="sm" variant="primary" loading={save.isPending} onClick={() => { setMsg(null); save.mutate(); }}>Salvar dispositivos</Button>
        {msg && <span role={msg.ok ? "status" : "alert"} className={clsx("text-body", msg.ok ? "text-ok" : "text-danger")}>{msg.text}</span>}
      </div>
    </div>
  );
}

function TagAssignmentEditor({ gw, tree }: { gw: Gateway; tree: TreePlant[] | undefined }) {
  const qc = useQueryClient();
  const tags = useQuery({ queryKey: ["tags", "all"], queryFn: () => tagsApi.list().then((r) => r.data) });
  const machines = useMemo(() => {
    const p = tree?.find((x) => x.id === gw.plant_id);
    return new Map((p?.lines ?? []).flatMap((l) => l.machines.map((m) => [m.id, `${l.name} · ${m.name}`] as const)));
  }, [tree, gw.plant_id]);
  const eligible = useMemo(() => (tags.data ?? []).filter((t: Tag) => t.machine_id && machines.has(t.machine_id) && (t.protocol === "modbus_tcp" || t.protocol === "opc_ua")), [tags.data, machines]);
  const [sel, setSel] = useState<Set<string> | null>(null);
  const current = useMemo(() => new Set(eligible.filter((t) => t.gateway_id === gw.id).map((t) => t.id)), [eligible, gw.id]);
  const chosen = sel ?? current;
  const [result, setResult] = useState<{ ok: boolean; text: string; warnings?: string[] } | null>(null);
  const save = useMutation({
    mutationFn: () => gatewaysApi.assignTags(gw.id, [...chosen].map((tag_id) => ({ tag_id }))).then((r) => r.data),
    onSuccess: (r) => {
      setResult({ ok: true, text: `${formatInt(r.assigned)} tag(s) atribuída(s). Configuração v${r.config_version} — o gateway recarrega sozinho.`, warnings: r.warnings });
      setSel(null);
      void qc.invalidateQueries({ queryKey: ["tags"] });
      void qc.invalidateQueries({ queryKey: gwKeys.all });
    },
    onError: (e) => setResult({ ok: false, text: apiErrorMessage(e, "Não foi possível atribuir as tags.") }),
  });
  if (tags.isPending) return <Skeleton className="h-32" />;
  if (tags.isError) return <ErrorState title="Não foi possível carregar as tags" error={tags.error} onRetry={() => void tags.refetch()} />;
  if (eligible.length === 0) return <p className="text-caption text-fg-subtle">Nenhuma tag Modbus TCP ou OPC UA em máquinas desta planta. Tags MQTT/HTTP não são lidas por gateway.</p>;
  const toggle = (id: string) => setSel(() => { const n = new Set(chosen); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const byMachine = [...new Set(eligible.map((t) => t.machine_id as string))];
  return (
    <div className="space-y-3" data-testid="gw-tags">
      <p className="text-caption text-fg-subtle">{chosen.size} de {eligible.length} tags deste gateway. Tags de outro gateway aparecem marcadas — atribuí-las aqui move a leitura para este.</p>
      <div className="max-h-80 space-y-3 overflow-y-auto rounded-xl border border-line p-3">
        {byMachine.map((mid) => (
          <fieldset key={mid} className="space-y-1">
            <legend className="text-caption font-semibold text-fg-muted">{machines.get(mid)}</legend>
            {eligible.filter((t) => t.machine_id === mid).map((t) => (
              <label key={t.id} className="flex min-h-10 items-center gap-3 rounded-lg px-2 hover:bg-surface-2/60">
                <input type="checkbox" checked={chosen.has(t.id)} onChange={() => toggle(t.id)} />
                <span className="min-w-0 flex-1 truncate text-body text-fg">{t.name}</span>
                <span className="shrink-0 text-caption text-fg-subtle">{t.protocol === "opc_ua" ? "OPC UA" : "Modbus"} · {t.address}</span>
                {t.gateway_id && t.gateway_id !== gw.id && <Badge tone="warn">outro gateway</Badge>}
              </label>
            ))}
          </fieldset>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" loading={save.isPending} disabled={sel === null} onClick={() => { setResult(null); save.mutate(); }}>Salvar atribuição</Button>
        {sel !== null && <Button variant="ghost" onClick={() => setSel(null)}>Desfazer</Button>}
      </div>
      {result && (
        <div role={result.ok ? "status" : "alert"} className={clsx("rounded-xl px-3 py-2 text-body", result.ok ? "bg-ok-solid/15 text-ok" : "bg-danger-solid/10 text-danger")}>
          {result.text}
          {result.warnings && result.warnings.length > 0 && <ul className="mt-1 list-disc pl-5 text-caption text-warn">{result.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
        </div>
      )}
    </div>
  );
}

function GatewayBody({ gw, tree, admin }: { gw: Gateway; tree: TreePlant[] | undefined; admin: boolean }) {
  const qc = useQueryClient();
  const now = useNow(5_000);
  const [token, setToken] = useState<EnrollToken | null>(null);
  const [err, setErr] = useState("");
  const hb = gw.last_heartbeat;
  const enroll = useMutation({
    mutationFn: () => gatewaysApi.enrollToken(gw.id).then((r) => r.data),
    onSuccess: (t) => { setToken(t); void qc.invalidateQueries({ queryKey: gwKeys.all }); },
    onError: (e) => setErr(apiErrorMessage(e, "Não foi possível gerar o código.")),
  });
  const revoke = useMutation({
    mutationFn: () => gatewaysApi.revoke(gw.id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: gwKeys.all }),
    onError: (e) => setErr(apiErrorMessage(e, "Não foi possível revogar.")),
  });
  const remove = useMutation({
    mutationFn: () => gatewaysApi.remove(gw.id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: gwKeys.all }),
    onError: (e) => setErr(apiErrorMessage(e, "Não foi possível remover.")),
  });
  const stale = hb?.config_version != null && hb.config_version !== gw.config_version;
  return (
    <div className="space-y-6">
      <section className="space-y-3" data-testid="gw-health">
        <div className="flex flex-wrap items-center gap-2">
          <GatewayStatusBadge status={gw.status} />
          <span className="text-caption text-fg-subtle">visto {seen(gw.last_seen_at, now)}</span>
        </div>
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {[
            ["Versão do edge", gw.version ?? "—"],
            ["Configuração", `v${gw.config_version}${hb?.config_version != null ? ` (gateway: v${hb.config_version})` : ""}`],
            ["Buffer local", hb?.buffer_depth != null ? `${formatInt(hb.buffer_depth)} leituras` : "—"],
            ["Em operação há", uptimeText(hb?.uptime_s)],
            ["Tags", formatInt(gw.tag_count)],
            ["Último heartbeat", hb?.at ? formatDateTime(hb.at) : "—"],
          ].map(([k, v]) => (
            <div key={k} className="rounded-lg bg-surface-2/60 px-3 py-2">
              <dt className="text-caption text-fg-subtle">{k}</dt>
              <dd className="text-body font-semibold tabular text-fg">{v}</dd>
            </div>
          ))}
        </dl>
        {stale && <p className="rounded-xl bg-warn-solid/10 px-3 py-2 text-body text-warn">O gateway ainda roda a configuração v{hb?.config_version}; a v{gw.config_version} será baixada no próximo ciclo.</p>}
        {hb?.errors && hb.errors.length > 0 && (
          <div className="rounded-xl bg-danger-solid/10 px-3 py-2 text-body text-danger">
            <p className="font-semibold">Erros relatados pelo gateway</p>
            <ul className="list-disc pl-5 text-caption">{hb.errors.map((e) => <li key={e}>{e}</li>)}</ul>
          </div>
        )}
        {gw.status === "pending" && <p className="text-caption text-fg-subtle">Ainda não cadastrado. Gere um código e rode os comandos no computador do gateway.</p>}
      </section>

      {admin && (
        <section className="space-y-3">
          <h3 className="text-overline uppercase text-fg-subtle">Cadastro do dispositivo</h3>
          <p className="text-caption text-fg-subtle">
            {gw.enrolled ? `Cadastrado (token ${gw.token_prefix ?? ""}…).` : "Sem token ativo."}
            {gw.enroll_pending && !token && ` Há um código válido até ${formatDateTime(gw.enroll_expires_at)} (o código em si só é mostrado quando é gerado).`}
          </p>
          {token ? <EnrollmentInfo gw={gw} token={token} /> : (
            <div className="flex flex-wrap gap-2">
              <Button variant={gw.enrolled ? "secondary" : "primary"} loading={enroll.isPending} onClick={() => { setErr(""); enroll.mutate(); }}>{gw.enrolled ? "Gerar novo código (reinstalação)" : "Gerar código de cadastro"}</Button>
              {gw.enrolled && <Button variant="danger-ghost" loading={revoke.isPending} onClick={() => { if (window.confirm("Revogar o token? O gateway para de enviar até ser cadastrado de novo.")) revoke.mutate(); }}>Revogar token</Button>}
            </div>
          )}
        </section>
      )}

      {admin && (
        <section className="space-y-3">
          <h3 className="text-overline uppercase text-fg-subtle">Dispositivos (CLPs)</h3>
          <DevicesEditor key={gw.id + gw.config_version} gw={gw} />
        </section>
      )}

      {admin ? (
        <section className="space-y-3">
          <h3 className="text-overline uppercase text-fg-subtle">Tags lidas por este gateway</h3>
          <TagAssignmentEditor gw={gw} tree={tree} />
        </section>
      ) : <p className="text-caption text-fg-subtle">Cadastro, dispositivos e atribuição de tags são feitos pelo administrador.</p>}

      {err && <p role="alert" className="text-body text-danger">{err}</p>}
      {admin && (
        <div className="border-t border-line pt-4">
          <Button variant="danger-ghost" loading={remove.isPending} onClick={() => { if (window.confirm(`Remover o gateway ${gw.name}? As tags perdem a atribuição.`)) remove.mutate(); }}>Remover gateway</Button>
        </div>
      )}
    </div>
  );
}

/** Admin/manager: field gateways fleet — live status, enrollment wizard, devices, tag assignment (docs/gateways.md). */
export default function GatewaysPage() {
  const { values: f, set } = useUrlFilters(["gw"] as const);
  const user = useAuthStore((s) => s.user);
  const admin = can(user, "gateways.admin");
  const tree = usePlantTree();
  const now = useNow(5_000);
  const [adding, setAdding] = useState(false);
  const list = useQuery({ queryKey: gwKeys.all, queryFn: () => gatewaysApi.list().then((r) => r.data), refetchInterval: 10_000, placeholderData: (p) => p });
  const open = (list.data ?? []).find((g) => g.id === f.gw) ?? null;
  const counts = (list.data ?? []).reduce<Record<GatewayStatus, number>>((acc, g) => { acc[g.status] += 1; return acc; }, { online: 0, degraded: 0, offline: 0, pending: 0 });
  const sorted = useMemo(() => {
    const rank: Record<GatewayStatus, number> = { offline: 0, degraded: 1, pending: 2, online: 3 };
    return (list.data ?? []).slice().sort((a, b) => rank[a.status] - rank[b.status] || a.name.localeCompare(b.name, "pt-BR"));
  }, [list.data]);

  return (
    <div className="mx-auto max-w-6xl space-y-5 animate-rise-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-title text-fg">Gateways de campo</h2>
          <p className="text-caption text-fg-subtle">Computadores no chão de fábrica que leem os CLPs e enviam os dados. A configuração é feita aqui e chega sozinha ao gateway.</p>
        </div>
        {admin && <Button variant="primary" onClick={() => setAdding(true)}>Adicionar gateway</Button>}
      </div>

      {list.data && list.data.length > 0 && (
        <div className="flex flex-wrap gap-2" aria-label="Resumo da frota">
          {(["offline", "degraded", "online", "pending"] as GatewayStatus[]).filter((s) => counts[s] > 0).map((s) => (
            <Badge key={s} tone={STATUS_TONE[s]} dot>{counts[s]} {GATEWAY_STATUS_LABEL[s].toLowerCase()}</Badge>
          ))}
        </div>
      )}

      <QueryView query={list} errorTitle="Não foi possível carregar os gateways"
        loading={<LoadingRegion label="Carregando gateways…"><div className="grid gap-3 md:grid-cols-2">{[0, 1].map((i) => <Skeleton key={i} className="h-36 rounded-xl" />)}</div></LoadingRegion>}
        isEmpty={(d) => d.length === 0}
        empty={<Card><EmptyState title="Nenhum gateway cadastrado" description="Cadastre o gateway de cada planta para receber os dados dos CLPs e gerenciar a configuração remotamente."
          action={admin ? <Button variant="primary" onClick={() => setAdding(true)}>Adicionar gateway</Button> : undefined} /></Card>}>
        {() => (
          <ul className="grid gap-3 md:grid-cols-2" data-testid="gw-list">
            {sorted.map((g) => (
              <li key={g.id}>
                <button type="button" onClick={() => set({ gw: g.id }, false)} data-testid="gw-card" data-status={g.status}
                  className={clsx("w-full rounded-xl border bg-surface p-4 text-left shadow-card transition-colors hover:border-line-strong",
                    g.status === "offline" ? "border-danger-solid/50" : g.status === "degraded" ? "border-warn-solid/50" : "border-line")}>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-heading text-fg">{g.name}</p>
                      <p className="text-caption text-fg-subtle">{plantName(tree.data, g.plant_id)}</p>
                    </div>
                    <GatewayStatusBadge status={g.status} />
                  </div>
                  <dl className="mt-3 grid grid-cols-3 gap-2 text-caption">
                    <div><dt className="text-fg-subtle">Visto</dt><dd className="font-medium text-fg">{seen(g.last_seen_at, now)}</dd></div>
                    <div><dt className="text-fg-subtle">Versão · config</dt><dd className="font-medium tabular text-fg">{g.version ?? "—"} · v{g.config_version}</dd></div>
                    <div><dt className="text-fg-subtle">Tags · buffer</dt><dd className="font-medium tabular text-fg">{formatInt(g.tag_count)} · {g.last_heartbeat?.buffer_depth != null ? formatInt(g.last_heartbeat.buffer_depth) : "—"}</dd></div>
                  </dl>
                </button>
              </li>
            ))}
          </ul>
        )}
      </QueryView>

      <Sheet open={!!open} onClose={() => set({ gw: null }, false)} size="lg" title={open?.name ?? "Gateway"} description={open ? plantName(tree.data, open.plant_id) : undefined}>
        {open && <GatewayBody key={open.id} gw={open} tree={tree.data} admin={admin} />}
      </Sheet>
      <AddGatewaySheet open={adding} onClose={() => setAdding(false)} onOpen={(id) => set({ gw: id }, false)} />
    </div>
  );
}
