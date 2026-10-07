import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { isEmergency, useNotificationStore } from "@/store/notifications";
import { useAuthStore } from "@/store/auth";
import { notificationTarget } from "@/utils/notifications";
import { can } from "@/utils/maintenance";
import { formatDateTime } from "@/utils/format";

type AudioCtor = typeof AudioContext;

let ctx: AudioContext | null = null;

/** Browsers only start audio after a user gesture: create/resume the context on the first tap. */
export function unlockAudio() {
  try {
    const Ctor: AudioCtor | undefined = window.AudioContext ?? (window as unknown as { webkitAudioContext?: AudioCtor }).webkitAudioContext;
    if (!Ctor) return;
    ctx ??= new Ctor();
    if (ctx.state === "suspended") void ctx.resume();
  } catch { /* no audio on this device */ }
}

function beep(at: number, freq: number, dur: number) {
  if (!ctx) return;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = "square";
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(0.0001, at);
  gain.gain.exponentialRampToValueAtTime(0.25, at + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, at + dur);
  osc.connect(gain).connect(ctx.destination);
  osc.start(at);
  osc.stop(at + dur + 0.02);
}

function siren() {
  unlockAudio();
  if (ctx) {
    const t = ctx.currentTime + 0.01;
    beep(t, 880, 0.22);
    beep(t + 0.3, 660, 0.22);
    beep(t + 0.6, 880, 0.22);
  }
  if (canVibrate()) {
    try { navigator.vibrate([400, 150, 400]); } catch { /* not supported */ }
  }
}

/** navigator.vibrate needs a user activation first (Chrome logs "Blocked call to navigator.vibrate" otherwise). */
export function canVibrate(nav: Navigator | undefined = typeof navigator === "undefined" ? undefined : navigator): boolean {
  if (!nav || typeof nav.vibrate !== "function") return false;
  const ua = (nav as Navigator & { userActivation?: { hasBeenActive: boolean } }).userActivation;
  return ua ? ua.hasBeenActive : false;
}

/**
 * Full-screen "chamado emergencial" alert with a repeating sound (workflows §3/§5: push + som, cannot be
 * silenced by preferences). Shows the oldest unacknowledged emergency; "Atender" opens the screen that
 * answers it.
 */
export default function EmergencyAlert({ mobile = false }: { mobile?: boolean }) {
  const emergencies = useNotificationStore((s) => s.emergencies);
  const dismiss = useNotificationStore((s) => s.dismiss);
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const current = emergencies[0];
  const actionRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const unlock = () => unlockAudio();
    window.addEventListener("pointerdown", unlock, { once: true });
    window.addEventListener("keydown", unlock, { once: true });
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, []);

  useEffect(() => {
    if (!current) return;
    siren();
    const t = setInterval(siren, 2500);
    actionRef.current?.focus();
    return () => clearInterval(t);
  }, [current]);

  if (!current || !isEmergency(current)) return null;
  const executor = can(user, "maintenance.execute");
  const target = notificationTarget(current, mobile || executor);

  return createPortal(
    <div role="alertdialog" aria-modal="true" aria-labelledby="emergency-title" aria-describedby="emergency-body"
      data-testid="emergency-alert"
      className="fixed inset-0 z-[60] flex flex-col items-center justify-center gap-6 bg-red-700 p-6 text-center text-white"
      style={{ backgroundColor: "rgb(185 28 28)" }}>
      <span aria-hidden="true" className="flex size-24 animate-pulse items-center justify-center rounded-full bg-white/15">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-14">
          <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <path d="M12 9v4M12 17h.01" />
        </svg>
      </span>
      <div className="max-w-xl space-y-2">
        <p className="text-overline uppercase tracking-widest text-white/80">Chamado emergencial</p>
        <h2 id="emergency-title" className="text-3xl font-bold leading-tight">{current.title}</h2>
        {current.body && <p id="emergency-body" className="text-lg text-white/90">{current.body}</p>}
        <p className="text-sm text-white/75">{formatDateTime(current.created_at)}</p>
        {emergencies.length > 1 && <p className="text-sm font-semibold">+{emergencies.length - 1} chamado(s) na fila</p>}
      </div>
      <div className="flex w-full max-w-md flex-col gap-3">
        <button ref={actionRef} type="button"
          onClick={() => { dismiss(current.id); navigate(target); }}
          style={{ color: "rgb(153 27 27)", backgroundColor: "#fff" }}
          className="min-h-16 w-full rounded-2xl px-6 text-xl font-bold shadow-lg focus:outline-none focus-visible:ring-4 focus-visible:ring-white/60">
          {executor ? "Atender chamado" : "Ver chamado"}
        </button>
        <button type="button" onClick={() => dismiss(current.id)}
          className="min-h-14 w-full rounded-2xl border-2 border-white/70 px-6 text-lg font-semibold text-white focus:outline-none focus-visible:ring-4 focus-visible:ring-white/60">
          Ciente — fechar alerta
        </button>
      </div>
    </div>,
    document.body,
  );
}
