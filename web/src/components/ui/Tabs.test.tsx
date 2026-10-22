import { useState } from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Tabs, TabPanel } from "./Tabs";

const ITEMS = [
  { value: "a", label: "Alfa" },
  { value: "b", label: "Beta" },
  { value: "c", label: "Gama" },
] as const;

function Harness() {
  const [v, setV] = useState<"a" | "b" | "c">("a");
  return (
    <>
      <Tabs id="t" label="Teste" items={ITEMS} value={v} onChange={setV} />
      <TabPanel id="t" activeValue={v}>painel {v}</TabPanel>
    </>
  );
}

describe("Tabs", () => {
  it("follows the WAI-ARIA tabs pattern with roving tabindex", () => {
    render(<Harness />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[0]).toHaveAttribute("tabindex", "0");
    expect(tabs[1]).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", "t-tab-a");
  });

  it("arrow keys / Home / End move selection and focus", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("tab", { name: "Alfa" }));
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Beta" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Beta" })).toHaveFocus();
    await user.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "Gama" })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Home}");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("painel a");
    await user.keyboard("{End}");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("painel c");
  });
});
