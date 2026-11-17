import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import GatewaysPage from "./GatewaysPage";
import { plantsApi, tagsApi } from "@/services/api";
import { DEFAULT_EDGE_IMAGE, EDGE_IMAGE, edgeImage, gatewaysApi, installCommands, type Gateway, type MqttInfo } from "@/services/gatewaysApi";
import { useAuthStore } from "@/store/auth";
import { plantTree } from "@/test/fixtures";
import { user } from "@/test/maintenanceFixtures";
import { ok, renderRoute } from "@/test/utils";
import type { Tag } from "@/services/types";

vi.mock("@/services/api", () => ({ plantsApi: { tree: vi.fn() }, tagsApi: { list: vi.fn() } }));
vi.mock("@/services/gatewaysApi", async (orig) => {
  const real = await orig<typeof import("@/services/gatewaysApi")>();
  return {
    ...real,
    gatewaysApi: { list: vi.fn(), get: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(), enrollToken: vi.fn(), revoke: vi.fn(), assignTags: vi.fn(), configPreview: vi.fn(), mqtt: vi.fn() },
  };
});

const ADMIN = user({ id: "u-adm", role: "admin", roles: ["admin"] });

function gw(over: Partial<Gateway> = {}): Gateway {
  return {
    id: "gw-1", tenant_id: "t1", plant_id: "plant-1", name: "Gateway Montagem", description: null, status: "online",
    last_seen_at: new Date(Date.now() - 12_000).toISOString(), version: "1.4.0", config_version: 7, config_fetched_at: null,
    enrolled: true, token_prefix: "gw_ab12", enroll_pending: false, enroll_expires_at: null, heartbeat_s: 10, pulse_poll_ms: 20,
    devices: [{ id: "modbus-plc", protocol: "modbus", host: "10.0.0.5", port: 502 }], tag_count: 1,
    last_heartbeat: { at: new Date().toISOString(), uptime_s: 7200, buffer_depth: 3, errors: [], config_version: 7 },
    mqtt_username: "gw-1234", created_at: "2026-10-01T00:00:00Z", ...over,
  };
}

function tag(over: Partial<Tag>): Tag {
  return {
    id: "tag-1", tenant_id: "t1", machine_id: "m-1", name: "ciclos A1", description: null, protocol: "modbus_tcp", address: "10",
    data_type: "int", unit: null, scale_factor: 1, offset: 0, min_value: null, max_value: null, poll_interval_ms: 1000,
    is_active: true, gateway_id: null, ...over,
  } as Tag;
}

beforeEach(() => {
  useAuthStore.setState({ user: ADMIN, loading: false, offline: false });
  vi.mocked(plantsApi.tree).mockResolvedValue(ok(plantTree()));
  vi.mocked(gatewaysApi.list).mockResolvedValue(ok([gw(), gw({ id: "gw-2", name: "Gateway Envase", status: "offline", last_seen_at: null, last_heartbeat: null })]));
  vi.mocked(tagsApi.list).mockResolvedValue(ok([
    tag({ id: "tag-1", gateway_id: "gw-1" }), tag({ id: "tag-2", name: "corrente motor", protocol: "opc_ua", address: "ns=2;s=I" }),
    tag({ id: "tag-3", name: "via mqtt", protocol: "mqtt" }),
  ]));
  vi.mocked(gatewaysApi.mqtt).mockResolvedValue(ok(mqttInfo()));
});

function mqttInfo(over: Partial<MqttInfo> = {}): MqttInfo {
  return {
    username: "gw-1234", topic_prefix: "factory/t1/", acl: "topic readwrite factory/t1/#",
    gateways_conf_line: "gw-1234 t1 $7$101$abc", revoked: false, managed: false, ...over,
  };
}

describe("Gateways", () => {
  it("lists the fleet, worst status first, with live info", async () => {
    renderRoute(<GatewaysPage />, "/settings/gateways", "/settings/gateways");
    const cards = await screen.findAllByTestId("gw-card");
    expect(cards.map((c) => c.getAttribute("data-status"))).toEqual(["offline", "online"]);
    expect(cards[1]).toHaveTextContent("há 12 s");
    expect(cards[1]).toHaveTextContent("1.4.0 · v7");
    expect(cards[0]).toHaveTextContent("nunca");
  });

  it("wizard: name + plant → creates, shows the enrollment code and copyable commands", async () => {
    vi.mocked(gatewaysApi.create).mockResolvedValue(ok(gw({ id: "gw-9", name: "Novo", status: "pending", enrolled: false })));
    vi.mocked(gatewaysApi.enrollToken).mockResolvedValue(ok({ gateway_id: "gw-9", code: "ABCD-EFGH-JKLM", expires_at: "2026-10-07T12:00:00Z" }));
    const u = userEvent.setup();
    renderRoute(<GatewaysPage />, "/settings/gateways", "/settings/gateways");
    await u.click((await screen.findAllByRole("button", { name: "Adicionar gateway" }))[0]);
    await u.type(screen.getByLabelText(/Nome/), "Novo");
    await u.click(screen.getByRole("button", { name: "Criar e gerar código" }));
    await waitFor(() => expect(gatewaysApi.create).toHaveBeenCalledWith({ name: "Novo", plant_id: "plant-1" }));
    expect(await screen.findByTestId("enroll-code")).toHaveTextContent("ABCD-EFGH-JKLM");
    expect(screen.getByTestId("cmd-run")).toHaveTextContent("ENROLL_CODE=ABCD-EFGH-JKLM");
    expect(screen.getByTestId("cmd-run")).toHaveTextContent(`SERVER_URL=${window.location.origin}`);
    expect(screen.getByTestId("cmd-run")).toHaveTextContent(EDGE_IMAGE);
    expect(screen.getByTestId("cmd-compose")).toHaveTextContent("ENROLL_CODE=ABCD-EFGH-JKLM");
    expect(within(screen.getByTestId("cmd-run")).getByRole("button", { name: "Copiar" })).toBeInTheDocument();
  });

  it("detail: health, then tag assignment of modbus/opc-ua tags of the plant (mqtt excluded)", async () => {
    vi.mocked(gatewaysApi.assignTags).mockResolvedValue(ok({ assigned: 2, config_version: 8, warnings: ["tag x: sem posição"] }));
    const u = userEvent.setup();
    renderRoute(<GatewaysPage />, "/settings/gateways?gw=gw-1", "/settings/gateways");
    const health = await screen.findByTestId("gw-health");
    expect(health).toHaveTextContent("3 leituras");
    expect(health).toHaveTextContent("2 h 0 min");
    const tags = await screen.findByTestId("gw-tags");
    expect(within(tags).queryByText("via mqtt")).toBeNull();
    await u.click(within(tags).getByLabelText(/corrente motor/));
    await u.click(within(tags).getByRole("button", { name: "Salvar atribuição" }));
    await waitFor(() => expect(gatewaysApi.assignTags).toHaveBeenCalledWith("gw-1", [{ tag_id: "tag-1" }, { tag_id: "tag-2" }]));
    expect(await within(tags).findByText(/Configuração v8/)).toBeInTheDocument();
    expect(within(tags).getByText("tag x: sem posição")).toBeInTheDocument();
  });

  it("manager sees the fleet read-only", async () => {
    useAuthStore.setState({ user: user({ role: "manager", roles: ["manager"] }) });
    renderRoute(<GatewaysPage />, "/settings/gateways?gw=gw-1", "/settings/gateways");
    expect(await screen.findByTestId("gw-health")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Adicionar gateway" })).toBeNull();
    expect(screen.queryByTestId("gw-tags")).toBeNull();
    expect(screen.getByText(/feitos pelo administrador/)).toBeInTheDocument();
  });

  it("install command: one step with ENROLL_CODE — no token, no MQTT password, the release image", () => {
    const c = installCommands({ origin: "https://app.example", code: "ABCD-EFGH-JKLM", mqttHost: "app.example" });
    expect(c.run).toContain("-e SERVER_URL=https://app.example");
    expect(c.run).toContain("-e ENROLL_CODE=ABCD-EFGH-JKLM");
    expect(c.run).toContain("-v fm-gateway-data:/data");
    expect(c.run).toContain("MQTT_BROKER_PORT=8883 -e MQTT_TLS=true");
    expect(c.run.trim().endsWith(EDGE_IMAGE)).toBe(true);
    expect(EDGE_IMAGE).toBe("ghcr.io/dayan77/factoryconnect-edge:0.6.0");
    for (const secret of ["GATEWAY_TOKEN", "MQTT_PASSWORD", "MQTT_USERNAME", "curl"]) expect(c.run).not.toContain(secret);
    expect(c.compose).toContain("gateway.env");
  });

  it("revoked gateway: notice with the broker action, and an enrolled gateway must be revoked before removal", async () => {
    vi.mocked(gatewaysApi.list).mockResolvedValue(ok([
      gw(),
      gw({ id: "gw-3", name: "Gateway Revogado", status: "pending", enrolled: false, token_prefix: null, mqtt_revoked: true,
        mqtt_action: "Remova a linha do usuário gw-1234 do gateways.conf do broker e recarregue-o." }),
    ]));
    renderRoute(<GatewaysPage />, "/settings/gateways?gw=gw-1", "/settings/gateways");
    expect(await screen.findByTestId("gw-remove-hint")).toHaveTextContent("revogue o gateway antes");
    expect(screen.queryByRole("button", { name: "Remover gateway" })).toBeNull();
    expect(screen.getByRole("button", { name: "Revogar gateway" })).toBeInTheDocument();
  });

  it("revoked gateway detail shows what to do on the broker and can be removed", async () => {
    vi.mocked(gatewaysApi.list).mockResolvedValue(ok([
      gw({ id: "gw-3", name: "Gateway Revogado", status: "pending", enrolled: false, token_prefix: null, mqtt_revoked: true,
        mqtt_action: "Remova a linha do usuário gw-1234 do gateways.conf do broker e recarregue-o." }),
    ]));
    renderRoute(<GatewaysPage />, "/settings/gateways?gw=gw-3", "/settings/gateways");
    expect(await screen.findByTestId("gw-mqtt-revoked")).toHaveTextContent("gateways.conf");
    expect(screen.getByRole("button", { name: "Remover gateway" })).toBeInTheDocument();
  });

  it("install command: plain MQTT 1883 when the app is not on https; image configurable by VITE_EDGE_IMAGE", () => {
    const c = installCommands({ origin: "http://localhost:5173", code: "X", mqttHost: "localhost" });
    expect(c.run).toContain("MQTT_BROKER_PORT=1883 -e MQTT_TLS=false");
    expect(DEFAULT_EDGE_IMAGE).toBe("ghcr.io/dayan77/factoryconnect-edge:0.6.0");
    expect(edgeImage(undefined)).toBe(DEFAULT_EDGE_IMAGE);
    expect(edgeImage("  ")).toBe(DEFAULT_EDGE_IMAGE);
    expect(edgeImage("registry.local/fc-edge:1.0")).toBe("registry.local/fc-edge:1.0");
  });

  it("devices: adds a ueaPowerDriver box (base_url + line), blocks an invalid URL and saves", async () => {
    vi.mocked(gatewaysApi.update).mockResolvedValue(ok(gw()));
    const u = userEvent.setup();
    renderRoute(<GatewaysPage />, "/settings/gateways?gw=gw-1", "/settings/gateways");
    const box = await screen.findByTestId("gw-devices");
    await u.click(within(box).getByRole("button", { name: "Adicionar dispositivo" }));
    await u.selectOptions(within(box).getByLabelText("Protocolo", { selector: "#dev-p-1" }), "ueapd");
    const url = within(box).getByLabelText("URL do box");
    await u.clear(url);
    await u.type(url, "http://admin:x@192.168.1.15:8003");
    expect(within(box).getByText("sem usuário/senha na URL")).toBeInTheDocument();
    expect(within(box).getByRole("button", { name: "Salvar dispositivos" })).toBeDisabled();
    await u.clear(url);
    await u.type(url, "http://192.168.1.15:8003");
    await u.clear(within(box).getByLabelText("Id", { selector: "#dev-id-1" }));
    await u.type(within(box).getByLabelText("Id", { selector: "#dev-id-1" }), "ueapd-m1");
    await u.type(within(box).getByLabelText("Linha (opcional)"), "hipot");
    await u.click(within(box).getByRole("button", { name: "Salvar dispositivos" }));
    await waitFor(() => expect(gatewaysApi.update).toHaveBeenCalledWith("gw-1", { devices: [
      { id: "modbus-plc", protocol: "modbus", host: "10.0.0.5", port: 502 },
      { id: "ueapd-m1", protocol: "ueapd", base_url: "http://192.168.1.15:8003", line: "hipot" },
    ] }));
    expect(await within(box).findByText("Dispositivos salvos.")).toBeInTheDocument();
  });

  it("device list shows ueapd boxes with their URL", async () => {
    vi.mocked(gatewaysApi.list).mockResolvedValue(ok([gw({ devices: [
      { id: "ueapd-m1", protocol: "ueapd", base_url: "http://192.168.1.15:8003", line: "hipot" },
      { id: "modbus-plc", protocol: "modbus", host: "10.0.0.5", port: 502 },
    ] })]));
    renderRoute(<GatewaysPage />, "/settings/gateways?gw=gw-1", "/settings/gateways");
    const list = await screen.findByTestId("gw-device-list");
    expect(list).toHaveTextContent("ueapd-m1Box ueaPowerDriver (HTTP)http://192.168.1.15:8003linha hipot");
    expect(list).toHaveTextContent("10.0.0.5:502");
    const editor = screen.getByTestId("gw-devices");
    expect(within(editor).getAllByTestId("gw-device").map((d) => d.getAttribute("data-protocol"))).toEqual(["ueapd", "modbus"]);
    expect(within(editor).getByLabelText("URL do box")).toHaveValue("http://192.168.1.15:8003");
  });

  it("MQTT managed by the server: no broker command, revoke confirmation says the broker follows by itself", async () => {
    vi.mocked(gatewaysApi.mqtt).mockResolvedValue(ok(mqttInfo({ managed: true })));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const u = userEvent.setup();
    renderRoute(<GatewaysPage />, "/settings/gateways?gw=gw-1", "/settings/gateways");
    const box = await screen.findByTestId("gw-mqtt");
    expect(box).toHaveAttribute("data-managed", "true");
    expect(box).toHaveTextContent("sincronizada automaticamente");
    expect(box).not.toHaveTextContent("gateways.conf");
    expect(screen.queryByTestId("gw-mqtt-line")).toBeNull();
    await u.click(screen.getByRole("button", { name: "Revogar gateway" }));
    expect(confirm.mock.calls[0][0]).toMatch(/recusar a credencial sozinho/);
    expect(confirm.mock.calls[0][0]).not.toMatch(/gateways\.conf/);
    expect(gatewaysApi.revoke).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("MQTT not managed: shows the gateways.conf line and the revoke confirmation asks to remove it", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const u = userEvent.setup();
    renderRoute(<GatewaysPage />, "/settings/gateways?gw=gw-1", "/settings/gateways");
    expect(await screen.findByTestId("gw-mqtt-line")).toHaveTextContent("gw-1234 t1 $7$101$abc");
    await u.click(screen.getByRole("button", { name: "Revogar gateway" }));
    expect(confirm.mock.calls[0][0]).toMatch(/gateways\.conf/);
    confirm.mockRestore();
  });

  it("revoked + managed: remove confirmation does not ask for a broker edit", async () => {
    vi.mocked(gatewaysApi.list).mockResolvedValue(ok([
      gw({ id: "gw-3", name: "Gateway Revogado", status: "pending", enrolled: false, token_prefix: null, mqtt_revoked: true,
        mqtt_action: "texto antigo do servidor" }),
    ]));
    vi.mocked(gatewaysApi.mqtt).mockResolvedValue(ok(mqttInfo({ revoked: true, managed: true, gateways_conf_line: null, acl: null, action: "O broker deixa de aceitar a credencial em até ~10 s." })));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const u = userEvent.setup();
    renderRoute(<GatewaysPage />, "/settings/gateways?gw=gw-3", "/settings/gateways");
    // the action of GET /mqtt (fresh) replaces the one in the list
    expect(await screen.findByText("O broker deixa de aceitar a credencial em até ~10 s.")).toBeInTheDocument();
    expect(screen.getByTestId("gw-mqtt-revoked")).not.toHaveTextContent("texto antigo");
    await u.click(screen.getByRole("button", { name: "Remover gateway" }));
    expect(confirm.mock.calls[0][0]).toMatch(/Remover o gateway Gateway Revogado/);
    expect(confirm.mock.calls[0][0]).not.toMatch(/gateways\.conf/);
    confirm.mockRestore();
  });

  it("tags: HTTP tags become assignable with a ueapd box and are saved with their source", async () => {
    vi.mocked(gatewaysApi.list).mockResolvedValue(ok([gw({ devices: [
      { id: "ueapd-m1", protocol: "ueapd", base_url: "http://192.168.1.15:8003" },
      { id: "ueapd-m2", protocol: "ueapd", base_url: "http://192.168.1.16:8003" },
    ] })]));
    vi.mocked(tagsApi.list).mockResolvedValue(ok([
      tag({ id: "tag-h1", name: "M1 run", protocol: "http", address: "state:run", gateway_id: "gw-1" }),
      tag({ id: "tag-h2", name: "M2 boas", protocol: "http", address: "passes:good" }),
    ]));
    vi.mocked(gatewaysApi.configPreview).mockResolvedValue(ok({
      gateway_id: "gw-1", revision: "7", config_version: 7, etag: "x", warnings: [],
      plant_map: { tags: [{ tag_id: "tag-h1", key: "state:run", source: "ueapd-m1" }] },
    }));
    vi.mocked(gatewaysApi.assignTags).mockResolvedValue(ok({ assigned: 2, config_version: 8, warnings: [] }));
    const u = userEvent.setup();
    renderRoute(<GatewaysPage />, "/settings/gateways?gw=gw-1", "/settings/gateways");
    const tags = await screen.findByTestId("gw-tags");
    expect(within(tags).getByText("HTTP · state:run")).toBeInTheDocument();
    await waitFor(() => expect(within(tags).getByLabelText("Box da tag M1 run")).toHaveValue("ueapd-m1"));
    await u.click(within(tags).getByLabelText(/M2 boas/));
    const save = within(tags).getByRole("button", { name: "Salvar atribuição" });
    expect(save).toBeDisabled();
    expect(within(tags).getByText("Escolha o box de 1 tag(s) HTTP.")).toBeInTheDocument();
    await u.selectOptions(within(tags).getByLabelText("Box da tag M2 boas"), "ueapd-m2");
    await u.click(save);
    await waitFor(() => expect(gatewaysApi.assignTags).toHaveBeenCalledWith("gw-1", [
      { tag_id: "tag-h1", source: "ueapd-m1" }, { tag_id: "tag-h2", source: "ueapd-m2" },
    ]));
  });
});
