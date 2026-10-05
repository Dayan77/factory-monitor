import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import StateTimeline from "./StateTimeline";
import { downtime, reasonCodes } from "@/test/fixtures";

const T = (h: number) => Date.UTC(2026, 9, 5, h);

describe("StateTimeline", () => {
  it("draws one strip per machine with state segments; stops are buttons with a described reason", () => {
    const onSelect = vi.fn();
    const ev = downtime({ start: new Date(T(9)).toISOString(), end: new Date(T(10)).toISOString(), reason_code: "EQP-03" });
    render(
      <StateTimeline tz="UTC" start={T(8)} stop={T(12)} now={T(11)} reasons={reasonCodes()} onSelect={onSelect}
        rows={[{ id: "m-1", name: "Prensa P-01", events: [ev] }, { id: "m-2", name: "Robô", events: [] }]} />,
    );
    const strips = screen.getAllByTestId("timeline-strip");
    expect(strips).toHaveLength(2);
    expect([...strips[0].children].map((c) => c.getAttribute("data-state"))).toEqual(["running", "error", "running", "nodata"]);
    const btn = screen.getByRole("button", { name: /Prensa P-01: Falha — Falha pneumática\/hidráulica.*1 h 00 min/ });
    fireEvent.focus(btn);
    expect(screen.getByRole("tooltip")).toHaveTextContent("código 17");
    fireEvent.click(btn);
    expect(onSelect).toHaveBeenCalledWith(ev);
    expect([...strips[1].children].map((c) => c.getAttribute("data-state"))).toEqual(["running", "nodata"]);
  });
});
