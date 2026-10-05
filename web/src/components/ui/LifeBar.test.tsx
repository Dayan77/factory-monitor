import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import LifeBar, { lifeBand } from "./LifeBar";

describe("lifeBand", () => {
  it.each([
    [0, "ok"], [0.699, "ok"], [0.7, "warn"], [0.9, "warn"], [0.901, "danger"], [1.2, "danger"],
  ] as const)("%s → %s", (v, band) => {
    expect(lifeBand(v)).toBe(band);
  });

  it("null / NaN mean no nominal life", () => {
    expect(lifeBand(null)).toBe("none");
    expect(lifeBand(undefined)).toBe("none");
    expect(lifeBand(Number.NaN)).toBe("none");
  });
});

describe("LifeBar", () => {
  it("renders an accessible progressbar with the percent and band", () => {
    const { container } = render(<LifeBar fraction={0.72} label="Vida — cilindro" />);
    const bar = screen.getByRole("progressbar", { name: "Vida — cilindro" });
    expect(bar).toHaveAttribute("aria-valuenow", "72");
    expect(screen.getByText("72,0 %")).toBeInTheDocument();
    expect(container.querySelector("[data-band]")).toHaveAttribute("data-band", "warn");
  });

  it("does not truncate values above 100% (fill is clamped, number is not)", () => {
    render(<LifeBar fraction={1.05} />);
    expect(screen.getByText("105,0 %")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "105");
  });

  it("shows an explicit label when there is no nominal life", () => {
    render(<LifeBar fraction={null} />);
    expect(screen.getByText("Sem vida nominal")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});
