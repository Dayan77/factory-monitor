import { describe, expect, it } from "vitest";
import { titleFor } from "./Header";

describe("titleFor", () => {
  it("nested factory routes keep the section title", () => {
    expect(titleFor("/factory")).toBe("Fábrica");
    expect(titleFor("/factory/machines/abc")).toBe("Fábrica");
    expect(titleFor("/dashboard")).toBe("Dashboard");
    expect(titleFor("/nope")).toBe("Factory Monitor");
  });
});
