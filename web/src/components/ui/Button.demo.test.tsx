import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import Button from "./Button";
import { useAuthStore } from "@/store/auth";

afterEach(() => useAuthStore.setState({ user: null }));

describe("Button in the public demo (sprint6 §5)", () => {
  it("disables write actions with the demo tooltip", () => {
    useAuthStore.setState({ user: { id: "u", name: "Visitante", email: "", role: "manager", roles: ["manager", "demo_viewer"], tenant_id: "t" } });
    render(<><Button variant="primary">Salvar</Button><Button type="submit">Enviar</Button><Button>Filtrar</Button><Button variant="primary" demoSafe>Perguntar</Button></>);
    for (const name of ["Salvar", "Enviar"]) {
      const b = screen.getByRole("button", { name });
      expect(b).toBeDisabled();
      expect(b).toHaveAttribute("title", "Modo demonstração: alterações desativadas");
    }
    expect(screen.getByRole("button", { name: "Filtrar" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Perguntar" })).toBeEnabled();
  });
  it("leaves buttons alone for a normal user", () => {
    useAuthStore.setState({ user: { id: "u", name: "Ana", email: "a@b.c", role: "admin", tenant_id: "t" } });
    render(<Button variant="primary">Salvar</Button>);
    expect(screen.getByRole("button", { name: "Salvar" })).toBeEnabled();
  });
});
