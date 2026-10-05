import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { authApi } from "@/services/api";
import {
  applyMachineStatus, applyMachineStatusToTree, applyUsageToMachineUsage, applyUsageToPosition,
  parseRealtimeMessage, realtimeSocketParams, reconnectActionFor,
} from "@/services/realtime";
import type { Machine, MachineUsage, PositionDetail, TreePlant, UsageMessage } from "@/services/types";
import { queryKeys } from "@/hooks/useAssets";
import { useRealtimeStore } from "@/store/realtime";

const RETRY_MS = 3000;

/**
 * Opens the tenant's realtime WebSocket once (mounted in Layout).
 * - authenticates with the access JWT (subprotocol ["bearer", token])
 * - sensor readings go to the realtime store (dashboard / sensors page)
 * - machine_status updates patch every cached ["machines", ...] query and the plants tree in place
 * - usage deltas patch the cached machine usage / position detail (live counters)
 * - close 4401 (token expired/invalid): refresh the token through the API
 *   client (its 401 interceptor rotates it, or logs out) and reconnect
 * - close 4403 (token of another tenant): stop, never retry
 */
export function useRealtimeConnection(tenantId: string | undefined) {
  const qc = useQueryClient();

  useEffect(() => {
    if (!tenantId) return;
    const { setStatus, pushReading, bumpUsage, reset } = useRealtimeStore.getState();
    let destroyed = false;
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let wasConnected = false;

    function scheduleRetry() {
      if (destroyed) return;
      retry = setTimeout(connect, RETRY_MS);
    }

    function applyUsage(msg: UsageMessage) {
      for (const [key, data] of qc.getQueriesData<MachineUsage>({ queryKey: queryKeys.machineUsageAll })) {
        const machineId = key[2];
        if (typeof machineId !== "string" || !data) continue;
        const next = applyUsageToMachineUsage(data, machineId, msg);
        if (next !== data) qc.setQueryData(key, next);
      }
      if (msg.position_id) {
        qc.setQueryData<PositionDetail>(queryKeys.position(msg.position_id), (old) => applyUsageToPosition(old, msg));
        bumpUsage(msg.position_id);
      } else if (msg.machine_id) {
        bumpUsage(msg.machine_id);
      }
    }

    function connect() {
      if (destroyed || !tenantId) return;
      const token = localStorage.getItem("access_token");
      if (!token) {
        setStatus("disconnected");
        return;
      }
      setStatus("connecting");
      const { url, protocols } = realtimeSocketParams(tenantId, token);
      ws = new WebSocket(url, protocols);

      ws.onopen = () => {
        if (destroyed) return;
        setStatus("connected");
        // after a reconnect, resync anything we may have missed while offline
        if (wasConnected) {
          void qc.invalidateQueries({ queryKey: ["machines"] });
          void qc.invalidateQueries({ queryKey: queryKeys.tree });
          void qc.invalidateQueries({ queryKey: ["usage"] });
          void qc.invalidateQueries({ queryKey: ["positions"] });
        }
        wasConnected = true;
      };

      ws.onmessage = (evt: MessageEvent<string>) => {
        if (destroyed) return;
        const msg = parseRealtimeMessage(evt.data);
        if (!msg) return;
        if (msg.type === "sensor_reading") {
          pushReading({ tag_id: msg.tag_id, value: msg.value, unit: msg.unit, timestamp: msg.timestamp });
        } else if (msg.type === "usage") {
          applyUsage(msg);
        } else {
          qc.setQueriesData<Machine[]>({ queryKey: ["machines"] }, (old) => applyMachineStatus(old, msg));
          qc.setQueryData<TreePlant[]>(queryKeys.tree, (old) => applyMachineStatusToTree(old, msg));
        }
      };

      ws.onclose = (evt: CloseEvent) => {
        if (destroyed) return;
        setStatus("disconnected");
        const action = reconnectActionFor(evt.code);
        if (action === "stop") return;
        if (action === "refresh-then-retry") {
          // Any authenticated call triggers the refresh interceptor on 401.
          authApi.me().then(scheduleRetry, scheduleRetry);
          return;
        }
        scheduleRetry();
      };

      ws.onerror = () => ws?.close();
    }

    connect();

    return () => {
      destroyed = true;
      clearTimeout(retry);
      if (ws) {
        const sock = ws;
        sock.onclose = null;
        sock.onerror = null;
        sock.onmessage = null;
        // closing a CONNECTING socket logs a browser error; close it once it opens instead
        if (sock.readyState === WebSocket.CONNECTING) sock.onopen = () => sock.close();
        else sock.close();
      }
      reset();
    };
  }, [tenantId, qc]);
}
