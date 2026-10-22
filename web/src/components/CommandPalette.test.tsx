import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeQueryClient, ok } from "@/test/utils";

vi.mock("@/services/api", async (orig) => {
  const m = await orig<typeof import("@/services/api")>();
  return {
    ...m,
    plantsApi: { tree: vi.fn(async () => ok([{ id: "p1", name: "Planta Manaus", timezone: "America/Manaus", lines: [
      { id: "l1", name: "Linha de Usinagem", status_summary: {}, machines: [{ id: "m1", name: "Torno CNC", status: "running", last_seen_at: null, positions_count: 1 }] },
    ] }])) },
    positionsApi: { ...m.positionsApi, listAll: vi.fn(async () => ok([{ id: "pos1", machine_id: "m1", name: "Rolamento do eixo", component_type: "bearing", cycles_per_part: 1 }])) },
  };
});
vi.mock("@/services/maintenanceApi", async (orig) => {
  const m = await orig<typeof import("@/services/maintenanceApi")>();
  return { ...m, workOrdersApi: { ...m.workOrdersApi, list: vi.fn(async () => ok([{ id: "w1", number: "OS-0042", title: "Troca do rolamento", state: "approved", machine_id: "m1" }])) } };
});

import CommandPalette, { PaletteButton, usePalette } from "./CommandPalette";
import { useAuthStore } from "@/store/auth";

function Where() { return <p data-testid="where">{useLocation().pathname + useLocation().search}</p>; }

function renderPalette() {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter initialEntries={["/dashboard"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <PaletteButton />
        <CommandPalette />
        <Routes><Route path="*" element={<Where />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  usePalette.setState({ open: false });
  useAuthStore.setState({ user: { id: "u1", name: "Ana", email: "a@x", role: "admin", roles: ["admin"], tenant_id: "t1" } });
});

describe("command palette (Ctrl+K)", () => {
  it("opens with Ctrl+K, finds a machine by an accent-less query and navigates with Enter", async () => {
    renderPalette();
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    const input = await screen.findByRole("combobox");
    expect(input).toHaveFocus();
    await userEvent.type(input, "torno");
    const option = await screen.findByRole("option", { name: /^Torno CNC/ });
    expect(input).toHaveAttribute("aria-activedescendant", option.id);
    await userEvent.keyboard("{Enter}");
    expect(screen.getByTestId("where")).toHaveTextContent("/factory/machines/m1");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("finds positions and work orders; arrows move the active option", async () => {
    renderPalette();
    act(() => usePalette.getState().setOpen(true));
    const input = await screen.findByRole("combobox");
    await userEvent.type(input, "rolamento");
    await screen.findByRole("option", { name: /OS-0042/ });
    const opts = screen.getAllByRole("option");
    expect(opts.map((o) => o.textContent)).toEqual([expect.stringContaining("Rolamento do eixo"), expect.stringContaining("OS-0042")]);
    expect(opts[0]).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowDown}");
    expect(opts[1]).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{Enter}");
    expect(screen.getByTestId("where")).toHaveTextContent("/maintenance/work-orders?wo=w1");
  });

  it("Esc closes and returns focus to the trigger; empty results are explained", async () => {
    renderPalette();
    const trigger = screen.getByTestId("palette-open");
    await userEvent.click(trigger);
    const input = await screen.findByRole("combobox");
    await userEvent.type(input, "zzzz-nada");
    await waitFor(() => expect(screen.getByText(/Nada encontrado/)).toBeInTheDocument());
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it('"/" opens it only outside text fields', async () => {
    renderPalette();
    const field = document.createElement("input");
    document.body.appendChild(field);
    field.focus();
    fireEvent.keyDown(field, { key: "/" });
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.keyDown(document.body, { key: "/" });
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    field.remove();
  });
});
