import type { ReactNode } from "react";

interface Props {
  /** Accessible summary of the chart. */
  label: string;
  height?: number;
  children: ReactNode;
  /** Data table behind the chart (shown on demand; always available to screen readers). */
  table: { headers: string[]; rows: (string | number)[][] };
}

export default function ChartFrame({ label, height = 240, children, table }: Props) {
  return (
    <figure className="m-0">
      <div role="img" aria-label={label} style={{ height }} className="w-full">
        {children}
      </div>
      <details className="mt-2 text-caption text-fg-subtle">
        <summary className="cursor-pointer select-none rounded px-1 py-0.5 hover:text-fg">Ver dados em tabela</summary>
        <div className="mt-2 max-h-56 overflow-auto">
          <table className="w-full text-caption">
            <thead>
              <tr>{table.headers.map((h) => <th key={h} scope="col" className="px-2 py-1 text-left font-medium text-fg-muted">{h}</th>)}</tr>
            </thead>
            <tbody>
              {table.rows.map((r, i) => (
                <tr key={i} className="border-t border-line">
                  {r.map((c, j) => <td key={j} className="px-2 py-1 tabular text-fg">{c}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
