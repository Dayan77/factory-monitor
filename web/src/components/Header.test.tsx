import { describe, expect, it } from "vitest";
import { titleFor } from "./Header";

describe("titleFor", () => {
  it("nested factory routes keep the section title", () => {
    expect(titleFor("/factory")).toBe("Mapa da planta");
    expect(titleFor("/factory/list")).toBe("Fábrica");
    expect(titleFor("/factory/machines/abc")).toBe("Fábrica");
    expect(titleFor("/dashboard")).toBe("Raio-X");
    expect(titleFor("/reports/shift")).toBe("Relatórios");
    expect(titleFor("/settings/production")).toBe("Configurações");
    expect(titleFor("/nope")).toBe("FactoryConnect");
  });
});
