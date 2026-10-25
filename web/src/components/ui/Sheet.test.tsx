import { useState } from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Sheet from "./Sheet";

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>abrir</button>
      <Sheet open={open} onClose={() => setOpen(false)} title="Detalhe" description="desc">
        <input aria-label="campo" />
        <button>ação</button>
      </Sheet>
    </>
  );
}

describe("Sheet", () => {
  it("opens as a labelled modal dialog and moves focus inside", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "abrir" }));
    const dialog = screen.getByRole("dialog", { name: "Detalhe" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleDescription("desc");
    expect(screen.getByRole("button", { name: "Fechar" })).toHaveFocus();
  });

  it("traps Tab focus and closes on Escape, restoring focus to the opener", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "abrir" });
    await user.click(opener);
    await user.tab(); // campo
    await user.tab(); // ação
    expect(screen.getByRole("button", { name: "ação" })).toHaveFocus();
    await user.tab(); // wraps to Fechar
    expect(screen.getByRole("button", { name: "Fechar" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });
});
