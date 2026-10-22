import { useEffect, useState } from "react";
import Button from "./Button";

/** Copies text to the clipboard (falls back to a hidden textarea when the async API is unavailable, e.g. http). */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* fall back */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

export default function CopyButton({ text, label = "Copiar", copiedLabel = "Copiado", size = "sm", className }: {
  text: string; label?: string; copiedLabel?: string; size?: "sm" | "md"; className?: string;
}) {
  const [state, setState] = useState<"idle" | "ok" | "fail">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const t = setTimeout(() => setState("idle"), 2000);
    return () => clearTimeout(t);
  }, [state]);
  return (
    <Button size={size} variant="secondary" className={className} aria-live="polite"
      onClick={() => void copyText(text).then((ok) => setState(ok ? "ok" : "fail"))}>
      {state === "ok" ? `✓ ${copiedLabel}` : state === "fail" ? "Não foi possível copiar" : label}
    </Button>
  );
}

/** Monospace block with a copy button (commands, codes). */
export function CodeBlock({ code, label, testId }: { code: string; label: string; testId?: string }) {
  return (
    <div className="space-y-1.5" data-testid={testId}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-caption font-medium text-fg-muted">{label}</p>
        <CopyButton text={code} label="Copiar" />
      </div>
      <pre className="max-w-full overflow-x-auto whitespace-pre rounded-lg border border-line bg-surface-2/60 p-3 font-mono text-caption text-fg">{code}</pre>
    </div>
  );
}
