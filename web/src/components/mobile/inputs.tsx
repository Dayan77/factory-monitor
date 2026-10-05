import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { inputClass } from "@/components/ui/Field";
import { formatDec1 } from "@/utils/format";

/** Big −/+ stepper for hours (gloves): 0.25 h steps, value editable. */
export function HoursStepper({ id, label, value, onChange, step = 0.25, min = 0 }: {
  id: string; label: string; value: number; onChange: (v: number) => void; step?: number; min?: number;
}) {
  const [text, setText] = useState(formatDec1(value));
  useEffect(() => { setText(formatDec1(value)); }, [value]);
  const set = (v: number) => onChange(Math.max(min, Math.round(v / step) * step));
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-caption font-medium text-fg-muted">{label}</label>
      <div className="flex items-stretch gap-2">
        <button type="button" aria-label={`Diminuir ${label}`} onClick={() => set(value - step)}
          className="flex min-h-14 w-16 items-center justify-center rounded-xl border-2 border-line-strong text-2xl font-bold text-fg active:bg-surface-2">−</button>
        <div className="relative flex-1">
          <input id={id} inputMode="decimal" value={text}
            onChange={(e) => setText(e.target.value)}
            onBlur={() => { const n = Number(text.replace(",", ".")); if (Number.isFinite(n)) set(n); else setText(formatDec1(value)); }}
            className={clsx(inputClass, "min-h-14 pr-8 text-center text-2xl font-semibold tabular")} />
          <span aria-hidden="true" className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-body text-fg-subtle">h</span>
        </div>
        <button type="button" aria-label={`Aumentar ${label}`} onClick={() => set(value + step)}
          className="flex min-h-14 w-16 items-center justify-center rounded-xl border-2 border-line-strong text-2xl font-bold text-fg active:bg-surface-2">+</button>
      </div>
    </div>
  );
}

interface DetectorLike {
  detect: (src: CanvasImageSource) => Promise<{ rawValue: string }[]>;
}
type DetectorCtor = new (opts?: { formats?: string[] }) => DetectorLike;

function detectorCtor(): DetectorCtor | null {
  const w = window as unknown as { BarcodeDetector?: DetectorCtor };
  return w.BarcodeDetector && typeof navigator.mediaDevices?.getUserMedia === "function" ? w.BarcodeDetector : null;
}

/**
 * Scan-friendly text input (serial / QR of a position): big monospace, uppercase, no autocorrect, and a
 * "Ler código" camera button where the browser has BarcodeDetector. Barcode-wedge scanners type + Enter.
 */
export function ScanInput({ id, label, value, onChange, onSubmit, placeholder, hint }: {
  id: string; label: string; value: string; onChange: (v: string) => void; onSubmit?: () => void; placeholder?: string; hint?: string;
}) {
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const Ctor = typeof window !== "undefined" ? detectorCtor() : null;

  useEffect(() => {
    if (!scanning || !Ctor) return;
    let stream: MediaStream | null = null;
    let stop = false;
    const detector = new Ctor({ formats: ["qr_code", "code_128", "code_39", "data_matrix", "ean_13"] });
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        const v = videoRef.current;
        if (!v) return;
        v.srcObject = stream;
        await v.play();
        while (!stop) {
          const codes = await detector.detect(v).catch(() => []);
          if (codes[0]?.rawValue) {
            onChange(codes[0].rawValue.trim());
            setScanning(false);
            return;
          }
          await new Promise((r) => setTimeout(r, 250));
        }
      } catch {
        setError("Não foi possível abrir a câmera. Digite o código.");
        setScanning(false);
      }
    })();
    return () => {
      stop = true;
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [scanning]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-caption font-medium text-fg-muted">{label}</label>
      <div className="flex gap-2">
        <input id={id} value={value} onChange={(e) => onChange(e.target.value.toUpperCase())}
          onKeyDown={(e) => { if (e.key === "Enter" && onSubmit) { e.preventDefault(); onSubmit(); } }}
          autoCapitalize="characters" autoCorrect="off" autoComplete="off" spellCheck={false} enterKeyHint="done"
          placeholder={placeholder}
          className={clsx(inputClass, "min-h-14 min-w-0 flex-1 font-mono text-lg tracking-wide")} />
        {Ctor && (
          <button type="button" onClick={() => { setError(null); setScanning(true); }}
            className="flex min-h-14 shrink-0 items-center gap-1.5 rounded-xl border-2 border-line-strong px-3 text-body font-medium text-fg">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-5" aria-hidden="true"><path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 12h10" /></svg>
            Ler
          </button>
        )}
      </div>
      {hint && !error && <p className="mt-1 text-caption text-fg-subtle">{hint}</p>}
      {error && <p role="alert" className="mt-1 text-caption text-danger">{error}</p>}
      {scanning && (
        <div className="fixed inset-0 z-[70] flex flex-col items-center justify-center gap-4 bg-black/90 p-4">
          <video ref={videoRef} muted playsInline className="max-h-[70dvh] w-full max-w-md rounded-xl" />
          <p className="text-body text-white">Aponte para a etiqueta</p>
          <button type="button" onClick={() => setScanning(false)} className="min-h-12 rounded-xl border-2 border-white/70 px-6 text-body font-semibold text-white">Cancelar</button>
        </div>
      )}
    </div>
  );
}

/** Large radio cards (one hand, gloves). */
export function BigChoice<V extends string>({ name, legend, options, value, onChange }: {
  name: string; legend: string; options: readonly { value: V; label: string }[]; value: V | ""; onChange: (v: V) => void;
}) {
  return (
    <fieldset>
      <legend className="mb-2 text-caption font-medium text-fg-muted">{legend}</legend>
      <div className="grid gap-2">
        {options.map((o) => (
          <label key={o.value} className={clsx("flex min-h-14 cursor-pointer items-center gap-3 rounded-xl border-2 px-4 text-body font-medium",
            value === o.value ? "border-accent bg-accent/10 text-fg" : "border-line text-fg-muted")}>
            <input type="radio" name={name} checked={value === o.value} onChange={() => onChange(o.value)} className="size-5 accent-[rgb(var(--c-accent))]" />
            {o.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
