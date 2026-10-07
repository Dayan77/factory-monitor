import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import Sheet from "@/components/ui/Sheet";
import Button from "@/components/ui/Button";
import EmptyState from "@/components/ui/EmptyState";
import QueryView from "@/components/ui/QueryView";
import { Tabs, TabPanel } from "@/components/ui/Tabs";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { mntKeys, useNotifications } from "@/hooks/useMaintenance";
import { notificationsApi } from "@/services/maintenanceApi";
import type { AppNotification } from "@/services/maintenanceTypes";
import { currentPushState, enablePush, type PushState } from "@/services/pwa";
import { isEmergency } from "@/store/notifications";
import { apiErrorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";
import {
  CHANNEL_LABEL, CHANNELS, notificationTarget, parsePreferences, serializePreferences, type Channel, type PrefModel,
} from "@/utils/notifications";

const BELL = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true">
    <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
  </svg>
);

function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => notificationsApi.read(id),
    onMutate: (id) => {
      qc.setQueryData<AppNotification[]>(mntKeys.notifications, (old) =>
        old?.map((n) => (n.id === id && !n.read_at ? { ...n, read_at: new Date().toISOString() } : n)));
    },
    onSettled: () => qc.invalidateQueries({ queryKey: mntKeys.notifications, exact: true }),
  });
}

function useMarkAll() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => notificationsApi.readAll(),
    onSuccess: () => {
      qc.setQueryData<AppNotification[]>(mntKeys.notifications, (old) =>
        old?.map((n) => (n.read_at ? n : { ...n, read_at: new Date().toISOString() })));
    },
  });
}

function NotificationList({ list, mobile, onNavigate }: { list: AppNotification[]; mobile: boolean; onNavigate: () => void }) {
  const navigate = useNavigate();
  const read = useMarkRead();
  const all = useMarkAll();
  const unread = list.filter((n) => !n.read_at).length;

  if (list.length === 0) {
    return <EmptyState title="Nenhuma notificação" description="Vencimentos, OS atribuídas e chamados aparecem aqui." />;
  }
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-caption text-fg-subtle">{unread ? `${unread} não lida${unread > 1 ? "s" : ""}` : "Tudo lido"}</p>
        <Button size="sm" variant="ghost" disabled={!unread} loading={all.isPending} onClick={() => all.mutate()}>Marcar todas como lidas</Button>
      </div>
      <ul className="space-y-2" aria-label="Notificações">
        {list.map((n) => (
          <li key={n.id}>
            <button type="button" data-testid="notification-item"
              onClick={() => {
                if (!n.read_at) read.mutate(n.id);
                onNavigate();
                navigate(notificationTarget(n, mobile));
              }}
              className={clsx("flex w-full min-h-14 items-start gap-3 rounded-xl border p-3 text-left transition-colors hover:bg-surface-2",
                n.read_at ? "border-line" : isEmergency(n) ? "border-danger-solid/50 bg-danger-solid/5" : "border-accent/40 bg-accent/5")}>
              <span aria-hidden="true" className={clsx("mt-1.5 size-2 shrink-0 rounded-full", n.read_at ? "bg-transparent" : isEmergency(n) ? "bg-danger-solid" : "bg-accent")} />
              <span className="min-w-0 flex-1">
                <span className={clsx("block text-body text-fg", !n.read_at && "font-semibold")}>{n.title}</span>
                {n.body && <span className="block text-caption text-fg-muted">{n.body}</span>}
                <span className="block text-caption text-fg-subtle">{formatDateTime(n.created_at)}{!n.read_at && <span className="sr-only"> · não lida</span>}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

const PUSH_TEXT: Record<PushState, string> = {
  unsupported: "Este navegador não suporta notificações push (no iPhone, instale o app na tela inicial).",
  denied: "As notificações foram bloqueadas no navegador. Libere nas configurações do site para receber chamados.",
  default: "Receba OS atribuídas e chamados emergenciais mesmo com o app fechado.",
  granted: "Permissão concedida — conclua a ativação.",
  subscribed: "Push ativo neste aparelho.",
};

function PushOptIn() {
  const [state, setState] = useState<PushState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { void currentPushState().then(setState); }, []);
  async function enable() {
    setBusy(true);
    setError(null);
    try {
      setState(await enablePush());
    } catch (err) {
      setError(err instanceof Error && !("isAxiosError" in err) ? err.message : apiErrorMessage(err, "Não foi possível ativar o push."));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-2 rounded-xl border border-line p-4">
      <p className="text-heading text-fg">Notificações no aparelho</p>
      <p className="text-caption text-fg-muted" data-testid="push-state">{state ? PUSH_TEXT[state] : "Verificando…"}</p>
      {(state === "default" || state === "granted") && (
        <Button variant="primary" loading={busy} onClick={() => void enable()}>Ativar notificações push</Button>
      )}
      {error && <p role="alert" className="text-caption text-danger">{error}</p>}
    </div>
  );
}

function Preferences() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: mntKeys.notificationPrefs, queryFn: () => notificationsApi.preferences().then((r) => parsePreferences(r.data)) });
  const [model, setModel] = useState<PrefModel | null>(null);
  useEffect(() => { if (q.data) setModel(q.data); }, [q.data]);
  const save = useMutation({
    mutationFn: (m: PrefModel) => notificationsApi.savePreferences(serializePreferences(m)),
    onSuccess: () => qc.invalidateQueries({ queryKey: mntKeys.notificationPrefs }),
  });
  const channels = useMemo(() => CHANNELS.filter((c) => model?.rows.some((r) => r.channels[c] !== undefined)), [model]);

  function toggle(event: string, c: Channel) {
    setModel((m) => m && { ...m, rows: m.rows.map((r) => (r.event === event ? { ...r, channels: { ...r.channels, [c]: !r.channels[c] } } : r)) });
  }

  return (
    <div className="space-y-4">
      <PushOptIn />
      <QueryView query={q} errorTitle="Não foi possível carregar as preferências" loading={<Skeleton className="h-40" />}
        isEmpty={(d) => d.rows.length === 0} empty={<EmptyState title="Sem preferências configuráveis" />}>
        {() => model && (
          <form onSubmit={(e) => { e.preventDefault(); save.mutate(model); }} className="space-y-3">
            <table className="w-full text-body">
              <caption className="sr-only">Canais por tipo de notificação</caption>
              <thead><tr className="text-caption text-fg-subtle">
                <th scope="col" className="py-1 text-left font-medium">Evento</th>
                {channels.map((c) => <th key={c} scope="col" className="w-16 py-1 text-center font-medium">{CHANNEL_LABEL[c]}</th>)}
              </tr></thead>
              <tbody className="divide-y divide-line">
                {model.rows.map((r) => (
                  <tr key={r.event}>
                    <th scope="row" className="py-2 pr-2 text-left font-normal text-fg">
                      {r.label}
                      {r.locked && <span className="block text-caption text-fg-subtle">Não pode ser silenciado (plantão)</span>}
                      {r.digest && !r.locked && <span className="block text-caption text-fg-subtle">E-mail vai no resumo das 07:00</span>}
                    </th>
                    {channels.map((c) => (
                      <td key={c} className="py-2 text-center">
                        {r.channels[c] === undefined ? <span className="text-fg-subtle">—</span> : (
                          <input type="checkbox" className="size-5 accent-[rgb(var(--c-accent))]" aria-label={`${r.label}: ${CHANNEL_LABEL[c]}`}
                            checked={!!r.channels[c]} disabled={r.locked} onChange={() => toggle(r.event, c)} />
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {model.digest_enabled != null && (
              <label className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border border-line px-3">
                <input type="checkbox" checked={model.digest_enabled} onChange={(e) => setModel((m) => m && { ...m, digest_enabled: e.target.checked })} className="size-5 accent-[rgb(var(--c-accent))]" />
                <span className="text-body text-fg">Resumo diário por e-mail às 07:00</span>
              </label>
            )}
            {save.isError && <p role="alert" className="text-caption text-danger">{apiErrorMessage(save.error, "Não foi possível salvar.")}</p>}
            {save.isSuccess && <p className="text-caption text-ok">Preferências salvas.</p>}
            <Button type="submit" variant="primary" loading={save.isPending}>Salvar preferências</Button>
          </form>
        )}
      </QueryView>
    </div>
  );
}

const TABS = [{ value: "list", label: "Notificações" }, { value: "prefs", label: "Preferências" }] as const;

/** Bell with unread count; opens the notification center (list + preferences + push opt-in). */
export default function NotificationBell({ mobile = false, className }: { mobile?: boolean; className?: string }) {
  const q = useNotifications();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"list" | "prefs">("list");
  const unread = (q.data ?? []).filter((n) => !n.read_at).length;
  const label = unread ? `Notificações: ${unread} não lida${unread > 1 ? "s" : ""}` : "Notificações";
  // live update (WebSocket): a NEW unread notification is announced to screen readers, politely, once
  const [announce, setAnnounce] = useState("");
  const [seen, setSeen] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (!q.data) return;
    const ids = new Set(q.data.map((n) => n.id));
    if (seen) {
      const fresh = q.data.filter((n) => !n.read_at && !seen.has(n.id));
      if (fresh.length) setAnnounce(fresh.length === 1 ? `Nova notificação: ${fresh[0].title}` : `${fresh.length} novas notificações`);
    }
    setSeen(ids);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data]);

  return (
    <>
      <button type="button" onClick={() => setOpen(true)} aria-label={label} data-testid="notification-bell"
        className={clsx("relative flex size-11 items-center justify-center rounded-lg text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg", className)}>
        {BELL}
        {unread > 0 && (
          <span aria-hidden="true" className="absolute right-1 top-1 flex min-w-5 items-center justify-center rounded-full bg-danger-solid px-1 text-[0.6875rem] font-bold leading-5 text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      <span className="sr-only" role="status" aria-live="polite">{announce}</span>
      <Sheet open={open} onClose={() => setOpen(false)} title="Notificações" size="md">
        <div className="space-y-4">
          <Tabs id="notif-tabs" label="Central de notificações" items={TABS} value={tab} onChange={setTab} size="sm" />
          <TabPanel id="notif-tabs" activeValue={tab}>
            {tab === "list" ? (
              <QueryView query={q} errorTitle="Não foi possível carregar as notificações"
                loading={<LoadingRegion label="Carregando notificações…"><div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16" />)}</div></LoadingRegion>}>
                {(list) => <NotificationList list={list} mobile={mobile} onNavigate={() => setOpen(false)} />}
              </QueryView>
            ) : <Preferences />}
          </TabPanel>
        </div>
      </Sheet>
    </>
  );
}
