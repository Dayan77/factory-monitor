import { describe, expect, it } from "vitest";
import { AxiosError, type AxiosResponse } from "axios";
import { apiErrorMessage } from "./errors";

function axiosErr(data: unknown) {
  const response = { data, status: 400, statusText: "", headers: {}, config: { headers: {} } } as AxiosResponse;
  return new AxiosError("bad", "ERR", undefined, undefined, response);
}

describe("apiErrorMessage", () => {
  it("uses a string detail", () => {
    expect(apiErrorMessage(axiosErr({ detail: "Slug já existe" }), "x")).toBe("Slug já existe");
  });
  it("uses the message of a structured detail (S3-4 PLN-03 → MNT-02)", () => {
    expect(apiErrorMessage(axiosErr({ detail: { message: "Use MNT-02", suggested_reason_code: "MNT-02" } }), "x"))
      .toBe("Use MNT-02");
  });
  it("joins validation messages", () => {
    expect(apiErrorMessage(axiosErr({ detail: [{ msg: "a" }, { msg: "b" }] }), "x")).toBe("a, b");
  });
  it("falls back for anything else", () => {
    expect(apiErrorMessage(new Error("boom"), "fallback")).toBe("fallback");
    expect(apiErrorMessage(axiosErr("html page"), "fallback")).toBe("fallback");
  });
});
