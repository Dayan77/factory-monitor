import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import MachineStatusBadge, { STATUS_ORDER, statusLabel } from "./MachineStatusBadge";
import type { MachineStatus } from "@/services/types";

describe("MachineStatusBadge", () => {
  it.each([
    ["running", "Operando"],
    ["stopped", "Parada"],
    ["maintenance", "Manutenção"],
    ["error", "Falha"],
    ["unknown", "Sem comunicação"],
  ] as const)("renders %s as %s", (status, label) => {
    render(<MachineStatusBadge status={status} />);
    expect(screen.getByText(label)).toHaveAttribute("data-status", status);
  });

  it("uses a smaller dot for size=sm", () => {
    const { container } = render(<MachineStatusBadge status="running" size="sm" />);
    expect(container.querySelector("[aria-hidden='true']")).toHaveClass("size-1.5");
  });

  it("falls back to 'Sem comunicação' for an unexpected status", () => {
    render(<MachineStatusBadge status={"exploded" as MachineStatus} />);
    expect(screen.getByText("Sem comunicação")).toHaveAttribute("data-status", "unknown");
  });

  it("orders statuses with the most actionable first and labels all of them", () => {
    expect(STATUS_ORDER[0]).toBe("error");
    expect(new Set(STATUS_ORDER).size).toBe(5);
    expect(STATUS_ORDER.map(statusLabel)).not.toContain(undefined);
  });
});
