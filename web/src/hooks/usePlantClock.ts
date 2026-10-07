import { useEffect } from "react";
import { usePlant } from "@/hooks/useEfficiency";
import { setDisplayTimeZone } from "@/utils/format";

/** App shells (desktop, technician phone, operator tablet): dates on screen follow the plant timezone. */
export function usePlantClock(): void {
  const { tree, tz } = usePlant();
  useEffect(() => { if (tree.data) setDisplayTimeZone(tz); }, [tree.data, tz]);
}
