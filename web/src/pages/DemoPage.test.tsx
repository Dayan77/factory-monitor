import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { AxiosError, AxiosHeaders } from "axios";
import DemoPage from "./DemoPage";
import { demoApi } from "@/services/sprint6Api";
import { useAuthStore } from "@/store/auth";
import { ok, renderRoute } from "@/test/utils";
import { DEMO_MSG_RATE_LIMIT, DEMO_MSG_UNAVAILABLE } from "@/hooks/useOpenDemo";

vi.mock("@/services/sprint6Api", () => ({ demoApi: { createSession: vi.fn() } }));
const createSession = vi.mocked(demoApi.createSession);

function httpError(status: number) {
  return new AxiosError("err", "ERR", undefined, undefined, {
    status, statusText: "", headers: {}, config: { headers: new AxiosHeaders() }, data: {},
  });
}

describe("DemoPage (/demo, target of the public site)", () => {
  beforeEach(() => {
    createSession.mockReset();
    localStorage.clear();
    useAuthStore.setState({ loadMe: vi.fn().mockResolvedValue(undefined) } as never);
  });

  it("opens the demo session once on load, stores the token and goes to the dashboard", async () => {
    localStorage.setItem("refresh_token", "old");
    createSession.mockResolvedValue(ok({ access_token: "jwt-demo", redirect_to: "/dashboard" }) as never);
    renderRoute(<DemoPage />, "/demo", "/demo");
    expect(screen.getByText("Abrindo demonstração…")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/dashboard"));
    expect(createSession).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem("access_token")).toBe("jwt-demo");
    expect(localStorage.getItem("refresh_token")).toBeNull();
  });

  it("explains 429 (rate limit) and 503 (unavailable) instead of a blank page", async () => {
    createSession.mockRejectedValueOnce(httpError(429));
    renderRoute(<DemoPage />, "/demo", "/demo");
    expect(await screen.findByRole("alert")).toHaveTextContent(DEMO_MSG_RATE_LIMIT);
    createSession.mockRejectedValueOnce(httpError(503));
    screen.getByTestId("retry-demo").click();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(DEMO_MSG_UNAVAILABLE));
    expect(screen.getByTestId("location")).toHaveTextContent("/demo");
  });
});
