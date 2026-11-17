import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AxiosError, type AxiosResponse } from "axios";
import LineTemplatePanel from "./LineTemplatePanel";
import { gatewaysApi, type Gateway } from "@/services/gatewaysApi";
import { lineTemplatesApi, type ApplyLineTemplateResult, type LineTemplate } from "@/services/lineTemplatesApi";
import { plantTree } from "@/test/fixtures";
import { makeQueryClient, ok, pending, renderWithClient } from "@/test/utils";

vi.mock("@/services/lineTemplatesApi", async (orig) => {
  const real = await orig<typeof import("@/services/lineTemplatesApi")>();
  return { ...real, lineTemplatesApi: { list: vi.fn(), apply: vi.fn() } };
});
vi.mock("@/services/gatewaysApi", async (orig) => {
  const real = await orig<typeof import("@/services/gatewaysApi")>();
  return { ...real, gatewaysApi: { list: vi.fn(), create: vi.fn(), enrollToken: vi.fn() } };
});

const TPL: LineTemplate = {
  key: "ueapd-led-driver", name: "Linha ueaPowerDriver (M1 Hipot + M2 ATS2)", description: "Boxes M1 e M2 do ueaPowerDriver.",
  machines: ["M1 Hipot", "M2 ATS2"],
  devices: [
    { id: "ueapd-m1", label: "Box M1 (hipot)", default_base_url: "http://192.168.1.15:8003" },
    { id: "ueapd-m2", label: "Box M2 (ATS2)", default_base_url: "http://192.168.1.16:8003" },
  ],
};

function result(over: Partial<ApplyLineTemplateResult> = {}): ApplyLineTemplateResult {
  return {
    line_id: "line-9", machines: [{ id: "m-91", name: "M1 Hipot" }, { id: "m-92", name: "M2 ATS2" }],
    tags_created: 14, tags_updated: 0, gateway: null, warnings: [], ...over,
  };
}

function gw(over: Partial<Gateway> = {}): Gateway {
  return {
    id: "gw-1", tenant_id: "t1", plant_id: "plant-1", name: "Gateway Montagem", description: null, status: "pending",
    last_seen_at: null, version: null, config_version: 1, config_fetched_at: null, enrolled: false, token_prefix: null,
    enroll_pending: false, enroll_expires_at: null, heartbeat_s: 10, pulse_poll_ms: 20, devices: [], tag_count: 0,
    last_heartbeat: null, mqtt_username: null, created_at: "2026-11-01T00:00:00Z", ...over,
  };
}

function apiError(status: number, detail: string) {
  return new AxiosError("x", String(status), undefined, undefined,
    { status, data: { detail }, statusText: "", headers: {}, config: { headers: {} } } as AxiosResponse);
}

const plant = plantTree()[0];

beforeEach(() => {
  vi.mocked(lineTemplatesApi.list).mockResolvedValue(ok([TPL]));
  vi.mocked(gatewaysApi.list).mockResolvedValue(ok([gw()]));
});

describe("LineTemplatePanel (onboarding step 2)", () => {
  it("shows a skeleton while the templates load", () => {
    vi.mocked(lineTemplatesApi.list).mockReturnValue(pending());
    renderWithClient(<LineTemplatePanel plant={plant} />);
    expect(screen.getByText("Carregando modelos…")).toBeInTheDocument();
  });

  it("explicit error when the templates cannot be loaded (no fake template)", async () => {
    vi.mocked(lineTemplatesApi.list).mockRejectedValue(apiError(500, "boom"));
    renderWithClient(<LineTemplatePanel plant={plant} />);
    expect(await screen.findByText("Não foi possível carregar os modelos de linha")).toBeInTheDocument();
    expect(screen.queryByTestId("tpl-apply")).toBeNull();
  });

  it("empty list: says there is no template", async () => {
    vi.mocked(lineTemplatesApi.list).mockResolvedValue(ok([]));
    renderWithClient(<LineTemplatePanel plant={plant} />);
    expect(await screen.findByTestId("tpl-empty")).toBeInTheDocument();
  });

  it("prefills line name and box URLs; applies without gateway and refreshes the plant tree", async () => {
    vi.mocked(lineTemplatesApi.apply).mockResolvedValue(ok(result({ warnings: ["M2 ATS2: sem tempo de ciclo ideal"] })));
    const client = makeQueryClient();
    const inv = vi.spyOn(client, "invalidateQueries");
    const u = userEvent.setup();
    renderWithClient(<LineTemplatePanel plant={plant} />, client);
    expect(await screen.findByLabelText("Nome da linha")).toHaveValue("Linha LED Driver");
    expect(screen.getByLabelText("Box M1 (hipot) — URL")).toHaveValue("http://192.168.1.15:8003");
    expect(screen.getByLabelText("Box M2 (ATS2) — URL")).toHaveValue("http://192.168.1.16:8003");
    expect(screen.getByRole("radio", { name: /Linha ueaPowerDriver/ })).toBeChecked();
    // without a gateway the URLs have nowhere to go: disabled, explained, not sent
    expect(screen.getByLabelText("Box M2 (ATS2) — URL")).toBeDisabled();
    expect(screen.getByTestId("tpl-urls-need-gw")).toBeInTheDocument();
    await u.click(screen.getByTestId("tpl-apply"));
    await waitFor(() => expect(lineTemplatesApi.apply).toHaveBeenCalledWith("ueapd-led-driver", {
      plant_id: "plant-1", line_name: "Linha LED Driver", gateway_id: null, devices: {},
    }));
    const res = await screen.findByTestId("tpl-result");
    expect(res).toHaveTextContent("Modelo aplicado: Linha LED Driver");
    expect(res).toHaveTextContent("Máquinas: M1 Hipot, M2 ATS2");
    expect(res).toHaveTextContent("Tags: 14 criada(s), 0 atualizada(s)");
    expect(res).toHaveTextContent("Sem gateway");
    expect(within(res).getByTestId("tpl-warnings")).toHaveTextContent("Aviso: M2 ATS2: sem tempo de ciclo ideal");
    const keys = inv.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    for (const k of [["plants", "tree"], ["lines"], ["machines"], ["gateways"]]) expect(keys).toContain(JSON.stringify(k));
    expect(gatewaysApi.create).not.toHaveBeenCalled();
  });

  it("validates the box URL (http(s)://host[:porta]) before calling the server", async () => {
    const u = userEvent.setup();
    renderWithClient(<LineTemplatePanel plant={plant} />);
    await u.selectOptions(await screen.findByLabelText("Gateway que lê os boxes (opcional)"), "__new__");
    const url = screen.getByLabelText("Box M1 (hipot) — URL");
    await u.clear(url);
    await u.type(url, "192.168.1.15:8003");
    expect(screen.getByText("use http:// ou https://")).toBeInTheDocument();
    await u.click(screen.getByTestId("tpl-apply"));
    expect(lineTemplatesApi.apply).not.toHaveBeenCalled();
    expect(screen.getByTestId("tpl-apply")).toBeDisabled();
    await u.clear(screen.getByLabelText("Nome da linha"));
    expect(screen.getByText("informe o nome da linha")).toBeInTheDocument();
  });

  it("with an existing gateway of the plant: sends gateway_id + box URLs and shows devices/tags assigned", async () => {
    vi.mocked(lineTemplatesApi.apply).mockResolvedValue(ok(result({
      line_name: "Linha LED Driver", line_created: false, tags_created: 0, tags_updated: 2, tags_unchanged: 12,
      gateway: { id: "gw-1", devices: [{ id: "ueapd-m1" }, { id: "ueapd-m2" }], tags_assigned: 14, config_version: 3 },
    })));
    const u = userEvent.setup();
    renderWithClient(<LineTemplatePanel plant={plant} />);
    const sel = await screen.findByLabelText("Gateway que lê os boxes (opcional)");
    await waitFor(() => expect(within(sel).getByRole("option", { name: "Gateway Montagem" })).toBeInTheDocument());
    await u.selectOptions(sel, "gw-1");
    await u.clear(screen.getByLabelText("Box M2 (ATS2) — URL"));
    await u.type(screen.getByLabelText("Box M2 (ATS2) — URL"), "http://10.0.0.16:8003");
    await u.click(screen.getByTestId("tpl-apply"));
    await waitFor(() => expect(lineTemplatesApi.apply).toHaveBeenCalledWith("ueapd-led-driver", {
      plant_id: "plant-1", line_name: "Linha LED Driver", gateway_id: "gw-1",
      devices: { "ueapd-m1": { base_url: "http://192.168.1.15:8003" }, "ueapd-m2": { base_url: "http://10.0.0.16:8003" } },
    }));
    const res = await screen.findByTestId("tpl-result");
    expect(res).toHaveTextContent("Modelo aplicado: Linha LED Driver (linha existente atualizada)");
    expect(res).toHaveTextContent("Tags: 0 criada(s), 2 atualizada(s), 12 sem mudança");
    expect(res).toHaveTextContent("Gateway Gateway Montagem — 2 dispositivo(s) configurado(s), 14 tag(s) atribuída(s) · configuração v3");
    expect(within(res).getByRole("button", { name: "Gerar código de instalação do gateway" })).toBeInTheDocument();
  });

  it("creates a gateway now, applies with it, and a failed apply is retried with the same gateway", async () => {
    vi.mocked(gatewaysApi.list).mockResolvedValue(ok([]));
    vi.mocked(gatewaysApi.create).mockResolvedValue(ok(gw({ id: "gw-new", name: "Gateway LED" })));
    vi.mocked(lineTemplatesApi.apply)
      .mockRejectedValueOnce(apiError(422, "Limite de máquinas do plano atingido (máx. 5)."))
      .mockResolvedValueOnce(ok(result({ gateway: { id: "gw-new", devices: ["ueapd-m1", "ueapd-m2"], tags_assigned: 14 } })));
    vi.mocked(gatewaysApi.enrollToken).mockResolvedValue(ok({ gateway_id: "gw-new", code: "ABCD-EFGH-JKLM", expires_at: "2026-11-18T12:00:00Z" }));
    const u = userEvent.setup();
    renderWithClient(<LineTemplatePanel plant={plant} />);
    await u.selectOptions(await screen.findByLabelText("Gateway que lê os boxes (opcional)"), "__new__");
    const name = screen.getByLabelText("Nome do novo gateway");
    await u.clear(name);
    await u.type(name, "Gateway LED");
    await u.click(screen.getByTestId("tpl-apply"));
    expect(await screen.findByTestId("tpl-error")).toHaveTextContent("Limite de máquinas do plano atingido (máx. 5).");
    expect(gatewaysApi.create).toHaveBeenCalledWith({ name: "Gateway LED", plant_id: "plant-1" });
    expect(screen.getByLabelText("Gateway que lê os boxes (opcional)")).toHaveValue("gw-new");
    await u.click(screen.getByTestId("tpl-apply"));
    await waitFor(() => expect(lineTemplatesApi.apply).toHaveBeenLastCalledWith("ueapd-led-driver", expect.objectContaining({ gateway_id: "gw-new" })));
    expect(gatewaysApi.create).toHaveBeenCalledTimes(1);
    const res = await screen.findByTestId("tpl-result");
    expect(res).toHaveTextContent("Gateway LED — 2 dispositivo(s) configurado(s), 14 tag(s) atribuída(s)");
    await u.click(within(res).getByRole("button", { name: "Gerar código de instalação do gateway" }));
    expect(await screen.findByTestId("enroll-code")).toHaveTextContent("ABCD-EFGH-JKLM");
    expect(screen.queryByTestId("tpl-error")).toBeNull();
  });

  it("gateway creation error is explicit", async () => {
    vi.mocked(gatewaysApi.create).mockRejectedValue(apiError(409, "Já existe um gateway com este nome."));
    const u = userEvent.setup();
    renderWithClient(<LineTemplatePanel plant={plant} />);
    await u.selectOptions(await screen.findByLabelText("Gateway que lê os boxes (opcional)"), "__new__");
    await u.click(screen.getByTestId("tpl-apply"));
    expect(await screen.findByTestId("tpl-error")).toHaveTextContent("Já existe um gateway com este nome.");
    expect(lineTemplatesApi.apply).not.toHaveBeenCalled();
  });
});
