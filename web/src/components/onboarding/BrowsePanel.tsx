import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import Button from "@/components/ui/Button";
import { inputClass } from "@/components/ui/Field";
import { browseApi } from "@/services/sprint6Api";
import type { Gateway } from "@/services/gatewaysApi";
import type { BrowseCommand } from "@/services/sprint6Types";
import type { TreePlant } from "@/services/types";
import { apiErrorMessage } from "@/utils/errors";
import {
  buildTree, isCounter, isVariable, ROLE_LABEL, SEMANTIC_ROLES, selectionToCsv, suggestKey, suggestRole,
  type BrowseSelection, type SemanticRole, type TreeNode,
} from "@/utils/onboarding";
import ImportPanel from "./ImportPanel";

const STATUS_TEXT: Record<string, string> = {
  pending: "aguardando o próximo heartbeat do gateway…",
  sent: "o gateway está navegando o servidor OPC UA…",
  done: "pronto",
  failed: "falhou",
  expired: "gateway não respondeu — está online?",
};

/**
 * OPC UA browse through the gateway (§4.2): a pulled command — the server never opens a connection into the plant.
 * The user ticks variables, gets a role suggestion (only a suggestion), picks machine/position and the selection
 * becomes the same tag CSV as an uploaded file (preview → confirm).
 */
export default function BrowsePanel({ gateway, plant, onConfirmed }: { gateway: Gateway; plant: TreePlant; onConfirmed?: () => void }) {
  const sources = (gateway.devices ?? []).filter((d) => d.protocol === "opcua");
  const [source, setSource] = useState(sources[0]?.id ?? "");
  const [depth, setDepth] = useState(4);
  const [req, setReq] = useState<string | null>(null);
  const [startErr, setStartErr] = useState("");
  const [starting, setStarting] = useState(false);
  const [sel, setSel] = useState<Map<string, BrowseSelection>>(new Map());
  const [csv, setCsv] = useState<string | null>(null);
  const machines = useMemo(() => plant.lines.flatMap((l) => l.machines.map((m) => ({ id: m.id, name: m.name, line: l.name }))), [plant]);

  const poll = useQuery({
    queryKey: ["gateway-browse", gateway.id, req],
    queryFn: () => browseApi.poll(gateway.id, req as string).then((r) => r.data),
    enabled: !!req,
    refetchInterval: (q) => {
      const s = (q.state.data as BrowseCommand | undefined)?.status;
      return s === "done" || s === "failed" || s === "expired" ? false : 2000;
    },
  });
  const cmd = poll.data;
  const tree = useMemo(() => (cmd?.status === "done" && cmd.result ? buildTree(cmd.result.nodes) : []), [cmd]);

  useEffect(() => { setSel(new Map()); setCsv(null); }, [req]);

  async function start() {
    setStarting(true);
    setStartErr("");
    try {
      const r = await browseApi.start(gateway.id, { source_id: source, max_depth: depth, max_nodes: 5000 });
      setReq(r.data.request_id);
    } catch (err) {
      setStartErr(apiErrorMessage(err, "Não foi possível pedir a navegação."));
    } finally {
      setStarting(false);
    }
  }

  function toggle(t: TreeNode) {
    setSel((prev) => {
      const next = new Map(prev);
      if (next.has(t.node.node_id)) next.delete(t.node.node_id);
      else {
        const m = machines[0];
        next.set(t.node.node_id, {
          node: t.node, role: suggestRole(t.node), line: m?.line ?? "", machine: m?.name ?? "", position: "",
          key: suggestKey(m?.line ?? "", m?.name ?? "", t.node), counterMax: "",
        });
      }
      return next;
    });
    setCsv(null);
  }
  function patch(id: string, p: Partial<BrowseSelection>) {
    setSel((prev) => {
      const next = new Map(prev);
      const cur = next.get(id);
      if (cur) {
        const upd = { ...cur, ...p };
        if (p.machine !== undefined) {
          const m = machines.find((x) => x.name === p.machine);
          upd.line = m?.line ?? upd.line;
          upd.key = suggestKey(upd.line, upd.machine, upd.node);
        }
        next.set(id, upd);
      }
      return next;
    });
    setCsv(null);
  }

  if (sources.length === 0) {
    return <p className="text-caption text-fg-muted">Este gateway não tem fonte OPC UA configurada (Gateways → dispositivos). Use o CSV de tags.</p>;
  }

  function renderNode(t: TreeNode, level: number): JSX.Element {
    const variable = isVariable(t.node);
    const checked = sel.has(t.node.node_id);
    return (
      <li key={t.node.node_id}>
        <div className="flex items-center gap-2 py-0.5" style={{ paddingLeft: level * 16 }}>
          {variable ? (
            <input type="checkbox" checked={checked} onChange={() => toggle(t)} aria-label={`Selecionar ${t.node.display_name ?? t.node.node_id}`}
              className="size-4 accent-[rgb(var(--c-accent))]" data-testid="browse-node" />
          ) : <span className="inline-block size-4 text-center text-fg-subtle" aria-hidden="true">▸</span>}
          <span className={clsx("text-caption", variable ? "text-fg" : "font-semibold text-fg-muted")}>{t.node.display_name ?? t.node.browse_name ?? t.node.node_id}</span>
          {variable && <span className="font-mono text-[11px] text-fg-subtle">{t.node.data_type} · {t.node.node_id}</span>}
          {variable && !checked && <span className="text-[11px] text-fg-subtle">sugestão: {ROLE_LABEL[suggestRole(t.node)]}</span>}
        </div>
        {t.children.length > 0 && <ul>{t.children.map((c) => renderNode(c, level + 1))}</ul>}
      </li>
    );
  }

  return (
    <div className="space-y-3" data-testid="browse-panel">
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-caption text-fg-muted">Fonte OPC UA
          <select value={source} onChange={(e) => setSource(e.target.value)} className={clsx(inputClass, "mt-1 w-auto")}>
            {sources.map((s) => <option key={s.id} value={s.id}>{s.id} ({String(s.endpoint ?? "")})</option>)}
          </select>
        </label>
        <label className="text-caption text-fg-muted">Profundidade
          <select value={depth} onChange={(e) => setDepth(Number(e.target.value))} className={clsx(inputClass, "mt-1 w-auto")}>
            {[1, 2, 3, 4, 5, 6].map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        <Button onClick={() => void start()} loading={starting} disabled={!source} data-testid="browse-start">Navegar pelo gateway</Button>
      </div>
      {startErr && <p role="alert" className="text-caption text-danger">{startErr}</p>}
      {cmd && (
        <p className={clsx("text-caption", cmd.status === "failed" || cmd.status === "expired" ? "text-danger" : "text-fg-muted")} role="status" data-testid="browse-status">
          {STATUS_TEXT[cmd.status] ?? cmd.status}{cmd.status === "done" && cmd.result ? `: ${cmd.result.nodes.length} nó(s)` : ""}{cmd.error ? ` — ${cmd.error}` : ""}
          {cmd.result?.truncated ? " · resultado cortado no limite de nós: navegue a partir de um nó mais específico" : ""}
        </p>
      )}
      {poll.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(poll.error, "Falha ao consultar a navegação.")}</p>}
      {tree.length > 0 && (
        <div className="grid gap-3 xl:grid-cols-2">
          <ul className="max-h-96 overflow-auto rounded-lg border border-line p-2" aria-label="Nós do servidor OPC UA">{tree.map((t) => renderNode(t, 0))}</ul>
          <div className="space-y-2">
            <p className="text-caption font-semibold text-fg">{sel.size} variável(is) selecionada(s)</p>
            <ul className="max-h-96 space-y-2 overflow-auto">
              {[...sel.values()].map((s) => (
                <li key={s.node.node_id} className="space-y-1 rounded-lg border border-line p-2">
                  <p className="font-mono text-[11px] text-fg-subtle">{s.node.node_id}</p>
                  <div className="grid gap-1 sm:grid-cols-2">
                    <select aria-label="Papel" value={s.role} onChange={(e) => patch(s.node.node_id, { role: e.target.value as SemanticRole })} className={clsx(inputClass, "min-h-9 py-1")}>
                      {SEMANTIC_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]} ({r})</option>)}
                    </select>
                    <select aria-label="Máquina" value={s.machine} onChange={(e) => patch(s.node.node_id, { machine: e.target.value })} className={clsx(inputClass, "min-h-9 py-1")}>
                      {machines.map((m) => <option key={m.id} value={m.name}>{m.line} / {m.name}</option>)}
                    </select>
                    <input aria-label="Posição (opcional)" placeholder="Posição (opcional)" value={s.position} onChange={(e) => patch(s.node.node_id, { position: e.target.value })} className={clsx(inputClass, "min-h-9 py-1")} />
                    <input aria-label="Chave" value={s.key} onChange={(e) => patch(s.node.node_id, { key: e.target.value })} className={clsx(inputClass, "min-h-9 py-1 font-mono")} />
                    {isCounter(s.role) && (
                      <input aria-label="Contador máximo" placeholder="contador_max (ex. 65535)" value={s.counterMax} onChange={(e) => patch(s.node.node_id, { counterMax: e.target.value.replace(/\D/g, "") })} className={clsx(inputClass, "min-h-9 py-1")} />
                    )}
                  </div>
                </li>
              ))}
            </ul>
            <Button disabled={sel.size === 0} onClick={() => setCsv(selectionToCsv(source, [...sel.values()]))} data-testid="browse-to-csv">Gerar prévia das tags</Button>
          </div>
        </div>
      )}
      {csv && <ImportPanel key={csv} kind="tags" targetId={gateway.id} initialCsv={csv} onConfirmed={onConfirmed} />}
    </div>
  );
}
