import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { makeQueryClient } from "@/test/utils";
import { effKeys } from "@/hooks/useEfficiency";
import { reasonCodesApi } from "@/services/api";
import {
  SNAPSHOT_MAX_AGE_MS, purgeOtherOwners, readSnapshot, seedFromSnapshot, tabletKeys, useSeedTabletSnapshot,
  useTabletSnapshot, writeSnapshot,
} from "./tabletCache";

vi.mock("@/services/api", () => ({ reasonCodesApi: { list: vi.fn() } }));
const list = vi.mocked(reasonCodesApi.list);

const OWNER = "t1:u1";
const REASONS = [{ code: "MEC-01", label: "Quebra", category: "MEC", planned: false, is_active: true }];
const STOPS = [{ id: "ev1", machine_id: "m1", start: "2026-10-07T08:00:00Z", end: null }];

describe("operator tablet offline snapshot (acceptance 5)", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("fm-user", JSON.stringify({ id: "u1", tenant_id: "t1" }));
    list.mockReset();
  });

  it("seeds reason codes and the stop list into a fresh (reopened) cache, so nothing waits on the network", () => {
    writeSnapshot(OWNER, effKeys.reasonCodes(false), REASONS, Date.now() - 60_000);
    writeSnapshot(OWNER, effKeys.downtime({ line_id: "L1", unjustified: true }), STOPS, Date.now() - 60_000);
    const qc = makeQueryClient();
    seedFromSnapshot(qc, OWNER, tabletKeys("L1"));
    expect(qc.getQueryData(effKeys.reasonCodes(false))).toEqual(REASONS);
    expect(qc.getQueryData(effKeys.downtime({ line_id: "L1", unjustified: true }))).toEqual(STOPS);
    // kept with the time it was saved: React Query still refetches when the network is back
    expect(qc.getQueryState(effKeys.reasonCodes(false))?.dataUpdatedAt).toBeLessThan(Date.now() - 50_000);
  });

  it("ignores snapshots older than the max age and those of other operators", () => {
    writeSnapshot(OWNER, effKeys.reasonCodes(false), REASONS, Date.now() - SNAPSHOT_MAX_AGE_MS - 1);
    expect(readSnapshot(OWNER, effKeys.reasonCodes(false))).toBeNull();
    writeSnapshot("t1:other", effKeys.reasonCodes(false), REASONS);
    purgeOtherOwners(OWNER);
    expect(readSnapshot("t1:other", effKeys.reasonCodes(false))).toBeNull();
  });

  it("prefetches reason codes on /op load and saves them; a reopened app offline gets them from the device", async () => {
    list.mockResolvedValue({ data: REASONS } as never);
    const qc = makeQueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
    renderHook(() => { useSeedTabletSnapshot(null); useTabletSnapshot("L1"); }, { wrapper });
    await waitFor(() => expect(readSnapshot(OWNER, effKeys.reasonCodes(false))?.data).toEqual(REASONS));
    expect(list).toHaveBeenCalledTimes(1);

    // App reopened with no network: a brand-new cache, the fetch never resolves.
    list.mockImplementation(() => new Promise(() => {}));
    const qc2 = makeQueryClient();
    const wrapper2 = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc2}>{children}</QueryClientProvider>;
    const { result } = renderHook(() => {
      useSeedTabletSnapshot(null);
      return useQuery({ queryKey: effKeys.reasonCodes(false), queryFn: () => list(false).then((r) => r.data) });
    }, { wrapper: wrapper2 });
    expect(result.current.isPending).toBe(false);
    expect(result.current.data).toEqual(REASONS);
  });
});
