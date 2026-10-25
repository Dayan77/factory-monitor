/**
 * Rendering of an assistant answer: safe Markdown subset, [n] citations linking to the exact screen (IA-2),
 * warnings (IA-3), refusals (IA-4/5/11), the "consultando dados…" tool steps and copy/share actions.
 */
import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";
import clsx from "clsx";
import Badge from "@/components/ui/Badge";
import CopyButton from "@/components/ui/CopyButton";
import Button from "@/components/ui/Button";
import type { AiChatResponse, Citation } from "@/services/aiApi";
import { parseBlocks, plainAnswer, REFUSAL_LABEL, toolLabel, type Inline } from "@/utils/aiFormat";

export interface Step {
  name: string;
  args: Record<string, unknown>;
  status: "running" | string;
  summary?: string;
  rows?: number | null;
}

function InlineView({ items, cites }: { items: Inline[]; cites: Map<number, Citation> }): JSX.Element {
  return (
    <>
      {items.map((it, i) => {
        if (it.kind === "text") return <Fragment key={i}>{it.text}</Fragment>;
        if (it.kind === "bold") return <strong key={i} className="font-semibold text-fg"><InlineView items={it.children} cites={cites} /></strong>;
        if (it.kind === "code") return <code key={i} className="rounded bg-surface-2 px-1 font-mono text-caption">{it.text}</code>;
        if (it.kind === "link") {
          return it.internal
            ? <Link key={i} to={it.href} className="font-medium text-ok underline-offset-2 hover:underline">{it.text}</Link>
            : <a key={i} href={it.href} target="_blank" rel="noopener noreferrer" className="font-medium text-ok underline-offset-2 hover:underline">{it.text}</a>;
        }
        const c = cites.get(it.n);
        return c ? (
          <Link key={i} to={c.url} data-testid="citation-ref" title={c.label} aria-label={`Fonte ${it.n}: ${c.label}`}
            className="mx-0.5 inline-flex min-w-[1.25rem] -translate-y-0.5 items-center justify-center rounded bg-accent/15 px-1 text-[0.6875rem] font-semibold leading-4 text-ok hover:bg-accent/25">
            {it.n}
          </Link>
        ) : <sup key={i} className="text-fg-subtle">[{it.n}]</sup>;
      })}
    </>
  );
}

export function Markdown({ text, citations }: { text: string; citations: Citation[] }) {
  const cites = new Map(citations.map((c) => [c.id, c]));
  const blocks = parseBlocks(text);
  return (
    <div className="space-y-2 text-body leading-relaxed text-fg">
      {blocks.map((b, i): ReactNode => {
        if (b.kind === "p") return <p key={i}><InlineView items={b.inlines} cites={cites} /></p>;
        if (b.kind === "h") return <p key={i} className="text-heading text-fg"><InlineView items={b.inlines} cites={cites} /></p>;
        if (b.kind === "ul" || b.kind === "ol") {
          const L = b.kind === "ul" ? "ul" : "ol";
          return <L key={i} className={clsx("space-y-1 pl-5", b.kind === "ul" ? "list-disc" : "list-decimal")}>{b.items.map((it, j) => <li key={j}><InlineView items={it} cites={cites} /></li>)}</L>;
        }
        return (
          <div key={i} className="-mx-1 overflow-x-auto">
            <table className="w-full min-w-max text-caption">
              <thead><tr>{b.header.map((h, j) => <th key={j} scope="col" className="border-b border-line px-2 py-1 text-left font-semibold text-fg-muted"><InlineView items={h} cites={cites} /></th>)}</tr></thead>
              <tbody>{b.rows.map((r, j) => <tr key={j} className="border-b border-line last:border-0">{r.map((c, k) => <td key={k} className="px-2 py-1 tabular"><InlineView items={c} cites={cites} /></td>)}</tr>)}</tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}

export function ToolSteps({ steps, live, verifying }: { steps: Step[]; live: boolean; verifying?: boolean }) {
  if (!steps.length && !live) return null;
  const list = (
    <ol className="space-y-1" data-testid="tool-steps">
      {steps.map((s, i) => (
        <li key={i} className="flex items-start gap-2 text-caption">
          {s.status === "running"
            ? <span aria-hidden="true" className="mt-0.5 size-3 shrink-0 animate-spin rounded-full border-2 border-accent border-t-transparent" />
            : <span aria-hidden="true" className={clsx("mt-0.5 shrink-0", s.status === "ok" ? "text-ok" : "text-warn")}>{s.status === "ok" ? "✓" : "!"}</span>}
          <span className="min-w-0 text-fg-muted">
            <span className="font-medium text-fg">{toolLabel(s.name)}{s.status === "running" ? "…" : ""}</span>
            {s.summary ? <> — {s.summary}</> : s.status !== "running" && s.status !== "ok" ? <> — {s.status}</> : null}
          </span>
        </li>
      ))}
      {live && verifying && (
        <li className="flex items-center gap-2 text-caption text-fg-muted">
          <span aria-hidden="true" className="size-3 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          Conferindo cada número com os dados…
        </li>
      )}
      {live && !steps.length && !verifying && (
        <li className="flex items-center gap-2 text-caption text-fg-muted">
          <span aria-hidden="true" className="size-3 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          Consultando dados…
        </li>
      )}
    </ol>
  );
  if (live) return <div role="status" aria-live="polite" className="rounded-lg bg-surface-2/60 px-3 py-2">{list}</div>;
  return (
    <details className="rounded-lg bg-surface-2/40 px-3 py-1.5">
      <summary className="cursor-pointer select-none text-caption text-fg-subtle hover:text-fg">Consultou {steps.length} fonte{steps.length === 1 ? "" : "s"} de dados</summary>
      <div className="mt-1.5 pb-1">{list}</div>
    </details>
  );
}

export function AnswerFooter({ r }: { r: AiChatResponse }) {
  const origin = window.location.origin;
  const text = plainAnswer(r.reply, r.citations, origin);
  const canShare = typeof navigator !== "undefined" && typeof navigator.share === "function";
  const reasons = (r.refusal_reasons?.length ? r.refusal_reasons : r.refusal_reason ? [r.refusal_reason] : []);
  return (
    <div className="space-y-2">
      {r.refused && reasons.length > 0 && (
        <div className="flex flex-wrap gap-1.5" data-testid="refusal">
          {reasons.map((x) => <Badge key={x} tone="info">{REFUSAL_LABEL[x] ?? x}</Badge>)}
        </div>
      )}
      {r.warnings.length > 0 && (
        <ul className="space-y-1 rounded-lg bg-warn-solid/10 px-3 py-2 text-caption text-warn" data-testid="answer-warnings">
          {r.warnings.map((w) => <li key={w}>⚠ {w}</li>)}
        </ul>
      )}
      {r.citations.length > 0 && (
        <div className="space-y-1" data-testid="answer-sources">
          <p className="text-overline uppercase text-fg-subtle">Fontes</p>
          <ol className="space-y-0.5 text-caption">
            {r.citations.map((c) => (
              <li key={c.id} className="flex gap-1.5"><span className="tabular text-fg-subtle">[{c.id}]</span><Link to={c.url} className="font-medium text-ok hover:underline">{c.label}</Link></li>
            ))}
          </ol>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        {r.answer_source === "system" ? null : r.verified
          ? <span className="text-caption text-ok" title="Cada número da resposta foi conferido com o resultado das consultas deste turno">✓ Números conferidos com os dados</span>
          : <span className="text-caption text-fg-subtle">Resposta sem números verificados</span>}
        {r.answer_source === "template" && <span className="text-caption text-fg-subtle">· modelo fixo</span>}
        <span className="ml-auto flex gap-1.5">
          <CopyButton text={text} label="Copiar" />
          {canShare && <Button size="sm" variant="ghost" onClick={() => void navigator.share({ title: "Resposta do assistente", text }).catch(() => undefined)}>Compartilhar</Button>}
        </span>
      </div>
    </div>
  );
}
