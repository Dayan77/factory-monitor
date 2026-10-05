import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { authApi } from "@/services/api";
import {
  applyMachineStatus, parseRealtimeMessage, realtimeSocketParams, reconnectActionFor,
} from "@/services/realtime";
import type { Machine } from "@/services/types";
import { useRealtimeStore } from "@/store/realtime";

const RETRY_MS = 3000;

/**
 * Opens the tenant's realtime WebSocket once (mounted in Layout).
 * - authenticates with the access JWT (subprotocol ["bearer", token])
 * - sensor readings go to the realtime store (dashboard / sensors page)
 * - machine_status updates patch every cached ["machines", ...] query in place
 * - close 4401 (token expired/invalid): refresh the token through the API
 *   client (its 401 interceptor rotates it, or logs out) and reconnect
 * - close 4403 (token of another tenant): stop, never retry
 */
export function useRealtimeConnection(tenantId: string | undefined) {
  const qc = useQueryClient();

  useEffect(() => {
    if (!tenantId) return;
    const { setStatus, pushReading, reset } = useRealtimeStore.getState();
    let destroyed = false;
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let wasConnected = false;

    function scheduleRetry() {
      if (destroyed) return;
      retry = setTimeout(connect, RETRY_MS);
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
        if (wasConnected) void qc.invalidateQueries({ queryKey: ["machines"] });
        wasConnected = true;
      };

      ws.onmessage = (evt: MessageEvent<string>) => {
        if (destroyed) return;
        const msg = parseRealtimeMessage(evt.data);
        if (!msg) return;
        if (msg.type === "sensor_reading") {
          pushReading({ tag_id: msg.tag_id, value: msg.value, unit: msg.unit, timestamp: msg.timestamp });
        } else {
          qc.setQueriesData<Machine[]>({ queryKey: ["machines"] }, (old) => applyMachineStatus(old, msg));
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
