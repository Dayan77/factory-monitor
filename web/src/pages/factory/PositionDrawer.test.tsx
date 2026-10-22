import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AxiosError, AxiosHeaders } from "axios";
import PositionDrawer from "./PositionDrawer";
import { componentsApi, positionsApi } from "@/services/api";
import { useAuthStore } from "@/store/auth";
import { component, positionDetail } from "@/test/fixtures";
import { ok, renderWithClient } from "@/test/utils";

vi.mock("@/services/api", () => ({
  positionsApi: { get: vi.fn(), install: vi.fn() },
  componentsApi: { list: vi.fn(), create: vi.fn(), remove: vi.fn() },
}));

const getPos = vi.mocked(positionsApi.get);
const install = vi.mocked(positionsApi.install);
const list = vi.mocked(componentsApi.list);
const create = vi.mocked(componentsApi.create);
const remove = vi.mocked(componentsApi.remove);

function asRole(role: string) {
  useAuthStore.setState({ user: { id: "u", name: "Ana Lima", email: "a@x", role, tenant_id: "t1" } });
}

const history = [
  component(),
  component({ id: "c-0", state: "removed", position_id: null, installed_at: "2026-01-01T00:00:00Z", removed_at: "2026-06-01T09:00:00Z", removal_reason: "failure", serial_number: "SN-000", life_consumed_pct: 0.6 }),
];

describe("PositionDrawer", () => {
  beforeEach(() => {
    getPos.mockResolvedValue(ok(positionDetail()));
    list.mockImplementation((params) =>
      Promise.resolve(ok(params.in_stock
        ? [component({ id: "c-9", state: "in_stock", position_id: null, installed_at: null, serial_number: "SN-009", model: "DSBC-63" }),
           component({ id: "c-motor", component_type: "electric_motor", state: "in_stock", position_id: null, manufacturer: "WEG", model: "W22" })]
        : history)));
  });

  it("shows installed component, life, projected end and history (read-only for operators)", async () => {
    asRole("operator");
    renderWithClient(<PositionDrawer positionId="pos-1" onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog", { name: "Cilindro de fixação" });
    expect((await within(dialog).findAllByText("Festo DSBC-50")).length).toBeGreaterThan(0);
    expect(within(dialog).getByText("SN-001")).toBeInTheDocument();
    expect(within(dialog).getByRole("progressbar", { name: "Vida consumida do componente instalado" })).toHaveAttribute("aria-valuenow", "72");
    expect(within(dialog).getByText("14/12/2026")).toBeInTheDocument();
    expect(within(dialog).getByText("70 dias")).toBeInTheDocument();
    expect(within(dialog).getByText("5.000.000")).toBeInTheDocument(); // nominal cycles
    const hist = await within(dialog).findByTestId("component-history");
    expect(within(hist).getByText("Falha")).toBeInTheDocument();
    expect(within(hist).getByText(/SN-000/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Remover" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Cadastrar novo componente" })).not.toBeInTheDocument();
  });

  it("admin removes the installed component with a reason", async () => {
    asRole("admin");
    remove.mockResolvedValue(ok(component({ state: "removed" })));
    const user = userEvent.setup();
    renderWithClient(<PositionDrawer positionId="pos-1" onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    await user.click(await within(dialog).findByRole("button", { name: "Remover" }));
    const confirm = within(dialog).getByRole("button", { name: "Confirmar remoção" });
    expect(confirm).toBeDisabled();
    await user.selectOptions(within(dialog).getByLabelText("Motivo da remoção"), "preventive_replacement");
    await user.click(confirm);
    expect(remove).toHaveBeenCalledWith("c-1", "preventive_replacement");
  });

  it("after removing, the drawer returns to view mode and offers install/register (remove -> install)", async () => {
    asRole("admin");
    // first fetch: installed; refetch after the remove invalidation: empty position
    getPos.mockResolvedValueOnce(ok(positionDetail()));
    getPos.mockResolvedValue(ok(positionDetail({ installed_component: null, component_usage: null })));
    install.mockResolvedValue(ok(positionDetail()));
    const user = userEvent.setup();
    const { client } = renderWithClient(<PositionDrawer positionId="pos-1" onClose={() => {}} />);
    // Live stack: other traffic (realtime events, polling) refreshes the position while the
    // POST /remove is in flight, so the form unmounts before the mutation settles — and the
    // response may only arrive after the user already opened the install form.
    let respond: () => void = () => {};
    remove.mockImplementation(async () => {
      await client.refetchQueries({ queryKey: ["positions"] });
      await new Promise<void>((r) => { respond = r; });
      return ok(component({ state: "removed" }));
    });
    const dialog = await screen.findByRole("dialog");
    await user.click(await within(dialog).findByRole("button", { name: "Remover" }));
    await user.selectOptions(within(dialog).getByLabelText("Motivo da remoção"), "preventive_replacement");
    await user.click(within(dialog).getByRole("button", { name: "Confirmar remoção" }));
    expect(await within(dialog).findByText("Nenhum componente instalado")).toBeInTheDocument();
    expect(await within(dialog).findByRole("button", { name: "Instalar do estoque" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Cadastrar novo componente" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Instalar do estoque" }));
    const select = await within(dialog).findByLabelText("Componente em estoque");
    const fetchesBefore = getPos.mock.calls.length;
    respond();                                   // the late POST /remove response
    await waitFor(() => expect(getPos.mock.calls.length).toBeGreaterThan(fetchesBefore));
    // the late success must not close the form the user opened meanwhile
    expect(within(dialog).getByLabelText("Componente em estoque")).toBe(select);
    await user.selectOptions(select, "c-9");
    await user.click(within(dialog).getByRole("button", { name: "Instalar" }));
    expect(install).toHaveBeenCalledWith("pos-1", "c-9");
  });

  it("admin installs a compatible component from stock into an empty position", async () => {
    asRole("admin");
    getPos.mockResolvedValue(ok(positionDetail({ installed_component: null, component_usage: null })));
    install.mockResolvedValue(ok(positionDetail()));
    const user = userEvent.setup();
    renderWithClient(<PositionDrawer positionId="pos-1" onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Nenhum componente instalado")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Instalar do estoque" }));
    const select = await within(dialog).findByLabelText("Componente em estoque");
    // only cylinders (same type as the position) are offered
    expect(within(select).queryByText(/WEG/)).not.toBeInTheDocument();
    await user.selectOptions(select, "c-9");
    await user.click(within(dialog).getByRole("button", { name: "Instalar" }));
    expect(install).toHaveBeenCalledWith("pos-1", "c-9");
  });

  it("admin registers a new component and installs it right away", async () => {
    asRole("admin");
    getPos.mockResolvedValue(ok(positionDetail({ installed_component: null, component_usage: null })));
    create.mockResolvedValue(ok(component()));
    const user = userEvent.setup();
    renderWithClient(<PositionDrawer positionId="pos-1" onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    await user.click(await within(dialog).findByRole("button", { name: "Cadastrar novo componente" }));
    await user.type(within(dialog).getByLabelText("Fabricante"), "Festo");
    await user.type(within(dialog).getByLabelText("Modelo"), "DSBC-50");
    await user.type(within(dialog).getByLabelText("Número de série"), "SN-777");
    await user.type(within(dialog).getByLabelText("Ciclos"), "5000000");
    await user.click(within(dialog).getByRole("button", { name: "Cadastrar" }));
    expect(create).toHaveBeenCalledWith({
      component_type: "pneumatic_cylinder", manufacturer: "Festo", model: "DSBC-50", serial_number: "SN-777",
      nominal_life: { cycles: 5000000 }, position_id: "pos-1",
    });
  });

  it("shows the API error when an action fails", async () => {
    asRole("admin");
    const err = new AxiosError("conflict", "ERR", undefined, undefined, {
      status: 409, statusText: "Conflict", headers: {}, config: { headers: new AxiosHeaders() },
      data: { detail: "Componente já instalado em outra posição." },
    });
    getPos.mockResolvedValue(ok(positionDetail({ installed_component: null, component_usage: null })));
    install.mockRejectedValue(err);
    const user = userEvent.setup();
    renderWithClient(<PositionDrawer positionId="pos-1" onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    await user.click(await within(dialog).findByRole("button", { name: "Instalar do estoque" }));
    await user.selectOptions(await within(dialog).findByLabelText("Componente em estoque"), "c-9");
    await user.click(within(dialog).getByRole("button", { name: "Instalar" }));
    expect(await within(dialog).findByText("Componente já instalado em outra posição.")).toBeInTheDocument();
  });

  it("renders nothing when no position is selected", () => {
    renderWithClient(<PositionDrawer positionId={null} onClose={() => {}} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
