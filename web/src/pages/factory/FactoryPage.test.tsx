import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { AxiosError } from "axios";
import FactoryPage from "./FactoryPage";
import { plantsApi } from "@/services/api";
import { plantTree } from "@/test/fixtures";
import { ok, pending, renderWithClient } from "@/test/utils";

vi.mock("@/services/api", () => ({ plantsApi: { tree: vi.fn() } }));
const tree = vi.mocked(plantsApi.tree);

describe("FactoryPage", () => {
  it("shows skeletons while loading (no fake data)", () => {
    tree.mockReturnValue(pending());
    renderWithClient(<FactoryPage />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByTestId("line-card")).not.toBeInTheDocument();
  });

  it("shows an explicit error with retry", async () => {
    tree.mockRejectedValueOnce(new AxiosError("Network Error"));
    renderWithClient(<FactoryPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Não foi possível carregar a fábrica");
    expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeInTheDocument();
  });

  it("shows an empty state when there are no plants", async () => {
    tree.mockResolvedValue(ok([]));
    renderWithClient(<FactoryPage />);
    expect(await screen.findByText("Nenhuma planta cadastrada")).toBeInTheDocument();
  });

  it("summarizes status and lists lines with the problem first, linking to drill-down", async () => {
    tree.mockResolvedValue(ok(plantTree()));
    renderWithClient(<FactoryPage />);
    const cards = await screen.findAllByTestId("line-card");
    // Linha 2 has a machine in error → first
    expect(cards[0]).toHaveTextContent("Linha 2 — Envase");
    expect(cards[0]).toHaveAttribute("href", "/factory/lines/line-2");
    expect(within(cards[0]).getByText("1 em falha")).toBeInTheDocument();
    expect(cards[1]).toHaveTextContent("Tudo operando");

    const summary = screen.getByRole("region", { name: "Resumo" });
    expect(within(summary).getByText("Em falha").parentElement?.parentElement).toHaveTextContent("1");
    expect(within(summary).getByText("/ 5")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Planta Manaus" })).toBeInTheDocument();
  });
});
