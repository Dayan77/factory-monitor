import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { AxiosError } from "axios";
import RaioXPage from "./RaioXPage";
import { efficiencyApi, plantsApi } from "@/services/api";
import { normalizeOverview } from "@/services/efficiency";
import { overviewPayload, plantTree } from "@/test/fixtures";
import { ok, pending, renderWithClient } from "@/test/utils";

vi.mock("@/services/api", () => ({
  plantsApi: { tree: vi.fn() },
  efficiencyApi: { overview: vi.fn() },
}));

describe("RaioXPage", () => {
  beforeEach(() => {
    vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
    vi.mocked(efficiencyApi.overview).mockResolvedValue(normalizeOverview(overviewPayload()));
  });

  it("shows one card per line, the line with an open stop first, with OEE and A/P/Q", async () => {
    renderWithClient(<RaioXPage />);
    const cards = await screen.findAllByTestId("line-card");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("Linha 2 — Envase");
    expect(cards[0]).toHaveTextContent("1 parada agora");
    expect(cards[0]).toHaveTextContent("Cobertura de dados 80,0 %");
    const l1 = cards[1];
    expect(within(l1).getByRole("img", { name: /^OEE hoje: 72,4\s%$/ })).toBeInTheDocument();
    expect(l1).toHaveTextContent("Turno A · OEE do turno 81,0 %");
    expect(l1).toHaveTextContent("1.843");
    expect(l1).toHaveTextContent("96,4");
    expect(within(l1).getByRole("link", { name: "2 sem justificativa" })).toHaveAttribute("href", "/stops?line=line-1");
    expect(within(l1).getByRole("link", { name: /Pior máquina: Prensa P-01/ })).toHaveAttribute("href", "/oee?scope=machine&id=m-1&period=today");
    expect(within(l1).getByRole("link", { name: "Linha 1 — Montagem" })).toHaveAttribute("href", "/oee?scope=line&id=line-1&period=today");
    expect(within(l1).getByRole("link", { name: "Modo TV" })).toHaveAttribute("href", "/tv/lines/line-1");
  });

  it("lists machines in fault from the live tree, top losses and components near end of life — all drillable", async () => {
    renderWithClient(<RaioXPage />);
    const fault = (await screen.findByRole("heading", { name: "Máquinas em falha" })).closest("div.surface") as HTMLElement;
    expect(await within(fault).findByRole("link", { name: /Envasadora E-01/ })).toHaveAttribute("href", "/factory/machines/m-3");
    const losses = (await screen.findByRole("heading", { name: "Maiores perdas de hoje" })).closest("div.surface") as HTMLElement;
    expect(within(losses).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      expect.stringContaining("Perda de velocidade"), expect.stringContaining("Falha pneumática"), expect.stringContaining("Setup"),
    ]);
    const eol = screen.getByRole("heading", { name: "Componentes perto do fim da vida" }).closest("div.surface") as HTMLElement;
    expect(within(eol).getByRole("link", { name: /Motor principal/ })).toHaveAttribute("href", "/factory/machines/m-1?pos=pos-2");
  });

  it("shows skeletons while the OEE is being computed", async () => {
    vi.mocked(efficiencyApi.overview).mockReturnValue(pending());
    renderWithClient(<RaioXPage />);
    expect(await screen.findByText("Calculando OEE das linhas…")).toBeInTheDocument();
    expect(screen.queryByTestId("line-card")).not.toBeInTheDocument();
  });

  it("shows an explicit error (never fake numbers) when the overview fails", async () => {
    vi.mocked(efficiencyApi.overview).mockRejectedValue(new AxiosError("Network Error"));
    renderWithClient(<RaioXPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Não foi possível calcular o Raio-X");
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("explains when the tenant has no plant", async () => {
    vi.mocked(plantsApi.tree).mockResolvedValue(ok([]));
    renderWithClient(<RaioXPage />);
    expect(await screen.findByText("Nenhuma planta cadastrada")).toBeInTheDocument();
  });
});
