import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { create } from "zustand";
import { usePlantTree } from "@/hooks/useAssets";
import { positionsApi } from "@/services/api";
import { workOrdersApi } from "@/services/maintenanceApi";
import { useAuthStore } from "@/store/auth";
import { useThemeStore } from "@/store/theme";
import { can, hasRole } from "@/utils/maintenance";
import { isDemoUser } from "@/utils/demo";
import { flatten, searchCommands, type CommandItem } from "@/utils/commandSearch";
import { visibleNav } from "./Sidebar";

/** Open state shared by the header button and the global shortcut. */
export const usePalette = create<{ open: boolean; setOpen: (v: boolean) => void }>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
}));

const WO_STATE: Record<string, string> = {
  requested: "Solicitada", approved: "Aprovada", scheduled: "Programada", in_progress: "Em execução",
  paused: "Pausada", completed: "Concluída", closed: "Encerrada", cancelled: "Cancelada",
};

function isTypingTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el) return false;
  return el.isContentEditable || el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT";
}

/** Ctrl+K / ⌘K anywhere, or "/" outside a text field, opens the palette. */
export function usePaletteShortcut(): void {
  const setOpen = usePalette((s) => s.setOpen);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(!usePalette.getState().open);
      } else if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey && !isTypingTarget(e.target) && !usePalette.getState().open) {
        e.preventDefault();
        setOpen(true);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [setOpen]);
}

/** Header trigger (also shows the shortcut). */
export function PaletteButton() {
  const setOpen = usePalette((s) => s.setOpen);
  const mac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
  return (
    <button type="button" onClick={() => setOpen(true)} aria-haspopup="dialog" aria-keyshortcuts="Control+K Meta+K"
      className="flex min-h-10 items-center gap-2 rounded-lg border border-line-strong bg-surface px-2.5 text-caption text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg sm:min-w-[11rem]"
      data-testid="palette-open">
      <svg viewBox="0 0 20 20" fill="currentColor" className="size-4 shrink-0" aria-hidden="true">
        <path fillRule="evenodd" d="M9 3.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11zM2 9a7 7 0 1 1 12.45 4.39l3.08 3.08a.75.75 0 1 1-1.06 1.06l-3.08-3.08A7 7 0 0 1 2 9z" clipRule="evenodd" />
      </svg>
      <span className="sr-only sm:not-sr-only">Buscar…</span>
      <kbd className="ml-auto hidden rounded border border-line px-1.5 font-sans text-[11px] text-fg-muted sm:inline">{mac ? "⌘" : "Ctrl"} K</kbd>
    </button>
  );
}

function useCommandItems(open: boolean): { items: CommandItem[]; loading: boolean } {
  const user = useAuthStore((s) => s.user);
  const tree = usePlantTree();
  const positions = useQuery({
    queryKey: ["palette", "positions"],
    queryFn: () => positionsApi.listAll().then((r) => r.data),
    enabled: open,
    staleTime: 5 * 60_000,
  });
  const canWo = can(user, "maintenance.view");
  const techOnly = hasRole(user, "technician") && !can(user, "maintenance.plan");
  const wos = useQuery({
    queryKey: ["palette", "work-orders"],
    queryFn: () => workOrdersApi.list().then((r) => r.data),
    enabled: open && canWo,
    staleTime: 60_000,
  });

  const items = useMemo(() => {
    const out: CommandItem[] = [];
    for (const g of visibleNav(user)) {
      for (const it of g.items) out.push({ id: `page:${it.to}`, kind: "page", label: it.label, hint: g.section, to: it.to });
    }
    if (!isDemoUser(user)) out.push({ id: "page:/account", kind: "page", label: "Minha conta", hint: "Perfil, senha, MFA, sessões", keywords: "perfil senha mfa sessoes lgpd privacidade", to: "/account" });
    out.push({ id: "action:theme", kind: "action", label: "Alternar tema claro/escuro", keywords: "tema dark light escuro claro", to: "#theme" });
    const machineName = new Map<string, string>();
    for (const p of tree.data ?? []) {
      for (const l of p.lines) {
        out.push({ id: `line:${l.id}`, kind: "line", label: l.name, hint: p.name, to: `/factory/lines/${l.id}` });
        for (const m of l.machines) {
          machineName.set(m.id, m.name);
          out.push({ id: `machine:${m.id}`, kind: "machine", label: m.name, hint: l.name, to: `/factory/machines/${m.id}` });
        }
      }
    }
    for (const pos of positions.data ?? []) {
      out.push({ id: `position:${pos.id}`, kind: "position", label: pos.name, hint: machineName.get(pos.machine_id) ?? undefined,
        keywords: pos.component_type, to: `/factory/machines/${pos.machine_id}?pos=${encodeURIComponent(pos.id)}` });
    }
    for (const wo of wos.data ?? []) {
      out.push({ id: `wo:${wo.id}`, kind: "work_order", label: `${wo.number} — ${wo.title}`,
        hint: [WO_STATE[wo.state] ?? wo.state, wo.machine_id ? machineName.get(wo.machine_id) : null].filter(Boolean).join(" · "),
        keywords: wo.number, to: techOnly ? `/m/work-orders/${wo.id}` : `/maintenance/work-orders?wo=${encodeURIComponent(wo.id)}` });
    }
    return out;
  }, [user, tree.data, positions.data, wos.data, techOnly]);

  return { items, loading: tree.isPending || positions.isFetching || wos.isFetching };
}

/**
 * Global search / command palette (WAI-ARIA combobox + listbox in a modal dialog): ↑/↓ move, Enter opens,
 * Esc closes, focus returns to where it was.
 */
export default function CommandPalette() {
  const open = usePalette((s) => s.open);
  const setOpen = usePalette((s) => s.setOpen);
  usePaletteShortcut();
  if (!open) return null;
  return <PaletteDialog onClose={() => setOpen(false)} />;
}

function PaletteDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const toggleTheme = useThemeStore((s) => s.toggle);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const baseId = useId();
  const listId = `${baseId}-list`;
  const { items, loading } = useCommandItems(true);
  const groups = useMemo(() => searchCommands(items, query), [items, query]);
  const flat = useMemo(() => flatten(groups), [groups]);

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    inputRef.current?.focus();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; opener?.focus?.(); };
  }, []);
  useEffect(() => { setActive(0); }, [query]);
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView?.({ block: "nearest" });
  }, [active]);

  function go(item: CommandItem | undefined) {
    if (!item) return;
    onClose();
    if (item.to === "#theme") { toggleTheme(); return; }
    navigate(item.to);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => (flat.length ? (i + 1) % flat.length : 0)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => (flat.length ? (i - 1 + flat.length) % flat.length : 0)); }
    else if (e.key === "Home" && e.ctrlKey) { e.preventDefault(); setActive(0); }
    else if (e.key === "End" && e.ctrlKey) { e.preventDefault(); setActive(Math.max(0, flat.length - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); go(flat[active]); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); }
    else if (e.key === "Tab") { e.preventDefault(); } // single focusable control: keep focus in the dialog
  }

  let index = -1;
  const activeId = flat[active] ? `${baseId}-opt-${active}` : undefined;
  const count = flat.length;

  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-start justify-center p-3 pt-[10vh] sm:p-6 sm:pt-[12vh]">
      <div aria-hidden="true" className="absolute inset-0 animate-fade-in bg-black/50" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label="Buscar no FactoryConnect" data-testid="palette"
        className="surface relative flex max-h-[70vh] w-full max-w-xl animate-rise-in flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-pop">
        <div className="flex items-center gap-2 border-b border-line px-3">
          <svg viewBox="0 0 20 20" fill="currentColor" className="size-5 shrink-0 text-fg-muted" aria-hidden="true">
            <path fillRule="evenodd" d="M9 3.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11zM2 9a7 7 0 1 1 12.45 4.39l3.08 3.08a.75.75 0 1 1-1.06 1.06l-3.08-3.08A7 7 0 0 1 2 9z" clipRule="evenodd" />
          </svg>
          <input
            ref={inputRef}
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={activeId}
            aria-autocomplete="list"
            aria-label="Buscar telas, linhas, máquinas, posições e ordens de serviço"
            placeholder="Buscar linha, máquina, posição, OS ou tela…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            className="min-h-12 flex-1 bg-transparent text-body text-fg placeholder:text-fg-subtle focus:outline-none"
            autoComplete="off"
            spellCheck={false}
          />
          <kbd className="rounded border border-line px-1.5 text-[11px] text-fg-muted">Esc</kbd>
        </div>
        <ul ref={listRef} id={listId} role="listbox" aria-label="Resultados" className="flex-1 overflow-y-auto p-2">
          {groups.map((g) => (
            <li key={g.kind} role="presentation">
              <p className="px-2 pb-1 pt-2 text-overline uppercase text-fg-muted" aria-hidden="true">{g.label}</p>
              <ul role="group" aria-label={g.label}>
                {g.items.map((it) => {
                  index += 1;
                  const i = index;
                  return (
                    <li key={it.id} id={`${baseId}-opt-${i}`} role="option" aria-selected={i === active} data-index={i}
                      onMouseMove={() => setActive(i)} onClick={() => go(it)}
                      className={clsx("flex cursor-pointer items-center justify-between gap-3 rounded-lg px-2 py-2 text-body",
                        i === active ? "bg-accent/15 text-fg" : "text-fg")}>
                      <span className="min-w-0 truncate">{it.label}</span>
                      {it.hint && <span className="shrink-0 truncate text-caption text-fg-muted">{it.hint}</span>}
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
          {count === 0 && (
            <li role="presentation" className="px-3 py-8 text-center text-body text-fg-muted">
              {loading ? "Carregando…" : `Nada encontrado para “${query}”.`}
            </li>
          )}
        </ul>
        <p className="sr-only" role="status" aria-live="polite">{count === 0 ? (loading ? "Carregando" : "Nenhum resultado") : `${count} resultado(s)`}</p>
        <div className="hidden items-center gap-3 border-t border-line px-3 py-2 text-caption text-fg-muted sm:flex">
          <span><kbd className="font-sans">↑</kbd> <kbd className="font-sans">↓</kbd> navegar</span>
          <span><kbd className="font-sans">Enter</kbd> abrir</span>
          <span><kbd className="font-sans">Esc</kbd> fechar</span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
