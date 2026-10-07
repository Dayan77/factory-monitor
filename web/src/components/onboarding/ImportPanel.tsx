import { useState } from "react";
import clsx from "clsx";
import Button from "@/components/ui/Button";
import Badge from "@/components/ui/Badge";
import { onboardingApi } from "@/services/sprint6Api";
import type { ImportPreview, ImportRow } from "@/services/sprint6Types";
import { apiErrorMessage } from "@/utils/errors";
import { saveBlob } from "@/utils/reports";
import { detectDelimiter } from "@/utils/onboarding";
import { isAxiosError } from "axios";

const STATUS: Record<ImportRow["status"], { tone: "ok" | "info" | "danger"; label: string }> = {
  ok: { tone: "ok", label: "ok" },
  update: { tone: "info", label: "atualiza" },
  error: { tone: "danger", label: "erro" },
};

/** Per-line result of a CSV preview (nothing is written until "Confirmar importação"). */
export function PreviewTable({ preview, kind }: { preview: ImportPreview; kind: "assets" | "tags" }) {
  return (
    <div className="space-y-2" data-testid="import-preview">
      <p className="text-caption text-fg-muted">
        {preview.rows.length} linha(s): {preview.summary.ok} nova(s), {preview.summary.update} atualizada(s), {preview.summary.error} com erro
        {preview.delimiter ? ` · separador “${preview.delimiter}”` : ""}
      </p>
      {preview.warnings && preview.warnings.length > 0 && (
        <ul className="text-caption text-warn">{preview.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
      )}
      <div className="max-h-80 overflow-auto rounded-lg border border-line">
        <table className="w-full text-left text-caption">
          <thead className="sticky top-0 bg-surface-2 text-fg-muted">
            <tr>
              <th scope="col" className="px-2 py-1.5 font-medium">Linha do arquivo</th>
              <th scope="col" className="px-2 py-1.5 font-medium">{kind === "tags" ? "Chave" : "Linha / máquina / posição"}</th>
              <th scope="col" className="px-2 py-1.5 font-medium">Resultado</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {preview.rows.map((r) => (
              <tr key={`${r.line}-${r.chave ?? r.maquina ?? ""}`} data-testid="preview-row" data-status={r.status}
                className={clsx(r.status === "error" && "bg-danger-solid/10")}>
                <td className="px-2 py-1.5 tabular text-fg-muted">{r.line}</td>
                <td className="px-2 py-1.5 font-mono text-fg">{kind === "tags" ? r.chave : [r.linha, r.maquina, r.posicao].filter(Boolean).join(" / ")}</td>
                <td className="px-2 py-1.5">
                  <span className="flex flex-wrap items-center gap-2">
                    <Badge tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Badge>
                    {r.message !== "ok" && <span className={clsx(r.status === "error" ? "text-danger" : "text-fg-muted")}>{r.message}</span>}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

interface Props {
  kind: "assets" | "tags";
  /** plant (assets) or gateway (tags) */
  targetId: string | null;
  onConfirmed?: (p: ImportPreview) => void;
  /** Pre-filled CSV (OPC UA browse selection). */
  initialCsv?: string;
}

/** CSV import of the wizard (§4 step 2 / §4.1): file or paste → preview per line → confirm (idempotent). */
export default function ImportPanel({ kind, targetId, onConfirmed, initialCsv = "" }: Props) {
  const [csv, setCsv] = useState(initialCsv);
  const [fileName, setFileName] = useState("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [busy, setBusy] = useState<"preview" | "confirm" | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  async function call(confirm: boolean) {
    if (!targetId) return;
    setBusy(confirm ? "confirm" : "preview");
    setError("");
    setDone("");
    try {
      const res = kind === "assets" ? await onboardingApi.importAssets(targetId, csv, confirm) : await onboardingApi.importTags(targetId, csv, confirm);
      setPreview(res.data);
      if (confirm) {
        setDone("Importação gravada.");
        onConfirmed?.(res.data);
      }
    } catch (err) {
      // 422 with the per-line result: show it, nothing was written
      const d = isAxiosError(err) ? (err.response?.data as { detail?: unknown } | undefined)?.detail : undefined;
      if (d && typeof d === "object" && "rows" in d) {
        setPreview(d as ImportPreview);
        setError("O arquivo tem erros: nada foi gravado.");
      } else setError(apiErrorMessage(err, "Não foi possível ler o arquivo."));
    } finally {
      setBusy(null);
    }
  }

  async function template() {
    try {
      const name = kind === "assets" ? "assets.csv" : "tags.csv";
      const r = await onboardingApi.template(name);
      saveBlob(r.data, `modelo_${name}`);
    } catch (err) {
      setError(apiErrorMessage(err, "Não foi possível baixar o modelo."));
    }
  }

  return (
    <div className="space-y-3" data-testid={`import-${kind}`}>
      <div className="flex flex-wrap items-center gap-2">
        <label className="inline-flex min-h-10 cursor-pointer items-center rounded-lg border border-line-strong bg-surface px-4 text-body font-medium text-fg hover:bg-surface-2">
          <input type="file" accept=".csv,text/csv,text/plain" className="sr-only" data-testid={`csv-file-${kind}`}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (!f) return;
              setFileName(f.name);
              void f.text().then((t) => { setCsv(t); setPreview(null); });
              e.target.value = "";
            }} />
          Escolher arquivo CSV
        </label>
        <Button variant="ghost" size="sm" demoSafe onClick={() => void template()}>Baixar modelo</Button>
        {fileName && <span className="text-caption text-fg-subtle">{fileName}</span>}
      </div>
      <label className="block text-caption text-fg-muted">
        Ou cole o conteúdo (separador “;” do Excel pt-BR ou “,”; campos com “;” entre aspas)
        <textarea value={csv} onChange={(e) => { setCsv(e.target.value); setPreview(null); }} rows={6} spellCheck={false}
          data-testid={`csv-text-${kind}`}
          className="mt-1 w-full rounded-lg border border-line-strong bg-surface px-3 py-2 font-mono text-caption text-fg focus:border-accent focus:outline-none" />
      </label>
      {csv.trim() && <p className="text-caption text-fg-subtle">Separador detectado: “{detectDelimiter(csv)}”</p>}
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => void call(false)} disabled={!csv.trim() || !targetId} loading={busy === "preview"} data-testid="csv-preview">Pré-visualizar</Button>
        <Button variant="primary" onClick={() => void call(true)} disabled={!preview?.can_confirm || !targetId} loading={busy === "confirm"} data-testid="csv-confirm">
          Confirmar importação
        </Button>
      </div>
      {!targetId && <p className="text-caption text-warn">{kind === "assets" ? "Conclua o passo 1 (planta) antes." : "Crie o gateway no passo 5 antes."}</p>}
      {error && <p role="alert" className="text-caption text-danger">{error}</p>}
      {done && <p role="status" className="text-caption text-ok">{done}</p>}
      {preview && <PreviewTable preview={preview} kind={kind} />}
    </div>
  );
}
