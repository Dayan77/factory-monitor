/**
 * PWA plumbing: service worker registration (offline app shell + web push) and push subscription.
 * The service worker never caches API responses — offline data lives in services/offline.ts, flagged.
 */
import { pushApi } from "./maintenanceApi";
import { parseNotification } from "./realtime";
import { isEmergency, useNotificationStore } from "@/store/notifications";

let registration: Promise<ServiceWorkerRegistration | null> | null = null;

export function swSupported(): boolean {
  return typeof navigator !== "undefined" && "serviceWorker" in navigator && window.isSecureContext;
}

/**
 * Registers /sw.js once. In dev (`vite`) the worker runs with `?dev=1`: push only, no caching
 * (Vite serves unbundled modules that must never be cached).
 */
export function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (registration) return registration;
  if (!swSupported()) return (registration = Promise.resolve(null));
  const url = import.meta.env.PROD ? "/sw.js" : "/sw.js?dev=1";
  // after the page has loaded and gone idle: installing the worker precaches the app chunks, which must not compete
  // with the first paint of the screen the technician / operator opened
  const idle = new Promise<void>((resolve) => {
    const go = () => {
      const ric = (window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number }).requestIdleCallback;
      if (ric) ric(() => resolve(), { timeout: 3000 }); else setTimeout(resolve, 1000);
    };
    if (document.readyState === "complete") go(); else window.addEventListener("load", go, { once: true });
  });
  registration = idle
    .then(() => navigator.serviceWorker.register(url, { scope: "/" }))
    .then((reg) => {
      navigator.serviceWorker.addEventListener("message", onWorkerMessage);
      return reg;
    })
    .catch(() => null);
  return registration;
}

/** A push arrived while the app is open: raise the emergency alert in-page too. */
function onWorkerMessage(evt: MessageEvent) {
  const data: unknown = evt.data;
  if (typeof data !== "object" || data === null || (data as { type?: unknown }).type !== "push") return;
  const n = parseNotification((data as { notification?: unknown }).notification);
  if (n && isEmergency(n)) useNotificationStore.getState().raise(n);
}

export type PushState = "unsupported" | "denied" | "default" | "subscribed" | "granted";

export function pushSupported(): boolean {
  return swSupported() && "PushManager" in window && "Notification" in window;
}

export async function currentPushState(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  if (Notification.permission === "denied") return "denied";
  const reg = await registerServiceWorker();
  const sub = await reg?.pushManager.getSubscription().catch(() => null);
  if (sub) return "subscribed";
  return Notification.permission === "granted" ? "granted" : "default";
}

export function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const b64 = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(b64);
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

/** Asks permission, subscribes with the server's VAPID key and registers the subscription. */
export async function enablePush(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  const perm = await Notification.requestPermission();
  if (perm !== "granted") return perm === "denied" ? "denied" : "default";
  const reg = await registerServiceWorker();
  if (!reg) return "unsupported";
  const keyRes = await pushApi.vapidKey();
  const raw = keyRes.data;
  const key = typeof raw === "string" ? raw : raw.public_key ?? raw.key;
  if (!key) throw new Error("O push ainda não foi configurado no servidor (chave VAPID ausente). Avise o administrador.");
  await navigator.serviceWorker.ready;
  const sub = (await reg.pushManager.getSubscription())
    ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(key) }));
  const json = sub.toJSON() as { endpoint?: string; keys?: { p256dh?: string; auth?: string } };
  if (!json.endpoint || !json.keys?.p256dh || !json.keys.auth) throw new Error("Assinatura de push inválida.");
  await pushApi.subscribe({ endpoint: json.endpoint, keys: { p256dh: json.keys.p256dh, auth: json.keys.auth } });
  return "subscribed";
}
