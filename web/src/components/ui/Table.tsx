import type { HTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from "react";
import clsx from "clsx";

/** Horizontal-scroll wrapper + table with token styles. */
export function Table({ className, caption, children, ...rest }: HTMLAttributes<HTMLTableElement> & { caption?: string }) {
  return (
    <div className="-mx-4 overflow-x-auto sm:mx-0">
      <table className={clsx("w-full min-w-[36rem] border-collapse text-body", className)} {...rest}>
        {caption && <caption className="sr-only">{caption}</caption>}
        {children}
      </table>
    </div>
  );
}

export function THead(props: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead {...props} className={clsx("border-b border-line", props.className)} />;
}

export function TBody(props: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody {...props} className={clsx("divide-y divide-line", props.className)} />;
}

export function TR({ className, interactive, ...rest }: HTMLAttributes<HTMLTableRowElement> & { interactive?: boolean }) {
  return (
    <tr
      className={clsx(interactive && "cursor-pointer transition-colors hover:bg-surface-2/60 focus-visible:bg-surface-2/60", className)}
      {...rest}
    />
  );
}

export function TH({ className, numeric, ...rest }: ThHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean }) {
  return (
    <th
      scope="col"
      className={clsx(
        "px-4 py-2.5 text-overline uppercase text-fg-subtle first:pl-4 sm:first:pl-3",
        numeric ? "text-right" : "text-left",
        className,
      )}
      {...rest}
    />
  );
}

export function TD({ className, numeric, ...rest }: TdHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean }) {
  return (
    <td
      className={clsx("px-4 py-3 align-middle text-fg first:pl-4 sm:first:pl-3", numeric && "text-right tabular", className)}
      {...rest}
    />
  );
}
