import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import SeverityBadge from "./SeverityBadge";
import type { Severity } from "@/services/types";

describe("SeverityBadge", () => {
  it.each([
    ["info", "Info", "text-blue-400"],
    ["warning", "Alerta", "text-yellow-400"],
    ["critical", "Crítico", "text-red-400"],
  ] as const)("renders %s as %s", (severity, label, cls) => {
    render(<SeverityBadge severity={severity} />);
    const el = screen.getByText(label);
    expect(el).toHaveClass(cls);
    expect(el).toHaveAttribute("data-severity", severity);
  });

  it("degrades an unexpected value from the API to info instead of crashing", () => {
    render(<SeverityBadge severity={"bogus" as Severity} />);
    expect(screen.getByText("Info")).toHaveAttribute("data-severity", "info");
  });
});
