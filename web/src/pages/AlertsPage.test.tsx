import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AxiosError, type AxiosResponse } from "axios";
import AlertsPage from "./AlertsPage";
import { alertsApi } from "@/services/api";
import { alertEvent } from "@/test/fixtures";
import { ok, renderWithClient } from "@/test/utils";

vi.mock("@/services/api", () => ({
  alertsApi: { listEvents: vi.fn(), resolveEvent: vi.fn() },
}));

const listEvents = vi.mocked(alertsApi.listEvents);

describe("AlertsPage", () => {
  it("shows an empty state instead of mock alerts", async () => {
    listEvents.mockResolvedValue(ok([]));
    renderWithClient(<AlertsPage />);
    expect(await screen.findByText("Nenhum alerta encontrado")).toBeInTheDocument();
    expect(screen.queryByText("Temp. Forno Alta")).not.toBeInTheDocument();
  });

  it("shows the HTTP status in the error state", async () => {
    const response = { status: 500, statusText: "", data: {}, headers: {}, config: { headers: {} } } as AxiosResponse;
    listEvents.mockRejectedValue(new AxiosError("fail", "ERR", undefined, undefined, response));
    renderWithClient(<AlertsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("erro 500");
  });

  it("lists alerts and filters active/resolved", async () => {
    listEvents.mockResolvedValue(ok([
      alertEvent({ id: "a-1", rule_name: "Temperatura alta", resolved_at: null }),
      alertEvent({ id: "a-2", rule_name: "Pressão baixa", severity: "warning", resolved_at: "2026-10-05T13:00:00Z" }),
    ]));
    renderWithClient(<AlertsPage />);
    expect(await screen.findByText("Temperatura alta")).toBeInTheDocument();
    expect(screen.getByText("Pressão baixa")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Ativos" }));
    expect(screen.queryByText("Pressão baixa")).not.toBeInTheDocument();
    expect(screen.getByText("Temperatura alta")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Resolvidos" }));
    expect(screen.getByText("Pressão baixa")).toBeInTheDocument();
    expect(screen.queryByText("Temperatura alta")).not.toBeInTheDocument();
  });
});
