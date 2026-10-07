import { describe, expect, it } from "vitest";
import { canVibrate } from "./EmergencyAlert";

describe("canVibrate (no 'Blocked call to navigator.vibrate' console errors)", () => {
  const vib = () => true;
  it("only vibrates after a user activation", () => {
    expect(canVibrate({ vibrate: vib, userActivation: { hasBeenActive: false } } as unknown as Navigator)).toBe(false);
    expect(canVibrate({ vibrate: vib, userActivation: { hasBeenActive: true } } as unknown as Navigator)).toBe(true);
    expect(canVibrate({ vibrate: vib } as unknown as Navigator)).toBe(false);
    expect(canVibrate({} as Navigator)).toBe(false);
    expect(canVibrate(undefined)).toBe(false);
  });
});
