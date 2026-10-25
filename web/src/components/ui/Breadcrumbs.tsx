import { Link } from "react-router-dom";

export interface Crumb {
  label: string;
  to?: string;
}

export default function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Navegação estrutural" className="min-w-0">
      <ol className="flex flex-wrap items-center gap-1 text-caption text-fg-subtle">
        {items.map((c, i) => {
          const last = i === items.length - 1;
          return (
            <li key={`${c.label}-${i}`} className="flex min-w-0 items-center gap-1">
              {c.to && !last ? (
                <Link to={c.to} className="truncate rounded px-1 py-0.5 hover:bg-surface-2 hover:text-fg">
                  {c.label}
                </Link>
              ) : (
                <span aria-current={last ? "page" : undefined} className="truncate px-1 py-0.5 font-medium text-fg-muted">
                  {c.label}
                </span>
              )}
              {!last && (
                <svg viewBox="0 0 20 20" fill="currentColor" className="size-3.5 shrink-0" aria-hidden="true">
                  <path fillRule="evenodd" d="M7.21 14.77a.75.75 0 0 1 .02-1.06L11.17 10 7.23 6.29a.75.75 0 1 1 1.04-1.08l4.5 4.25a.75.75 0 0 1 0 1.08l-4.5 4.25a.75.75 0 0 1-1.06-.02z" clipRule="evenodd" />
                </svg>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
