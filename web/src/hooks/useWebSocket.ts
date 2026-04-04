import { useEffect, useRef, useState } from "react";
import type { SensorReading } from "@/services/types";

type WsStatus = "connecting" | "connected" | "disconnected";

export function useRealtimeSensors(tenantId: string) {
  const [readings, setReadings] = useState<Map<string, SensorReading>>(new Map());
  const [status, setStatus] = useState<WsStatus>("disconnected");
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    let destroyed = false;

    function connect() {
      if (destroyed) return;
      setStatus("connecting");

      const protocol = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${protocol}://${location.host}/ws/sensors/${tenantId}`);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!destroyed) setStatus("connected");
      };

      ws.onmessage = (evt) => {
        if (destroyed) return;
        try {
          const reading: SensorReading = JSON.parse(evt.data);
          setReadings((prev) => new Map(prev).set(reading.tag_id, reading));
        } catch {
          // ignore malformed messages
        }
      };

      ws.onclose = () => {
        if (!destroyed) {
          setStatus("disconnected");
          retryRef.current = setTimeout(connect, 3000);
        }
      };

      ws.onerror = () => ws.close();
    }

    connect();

    return () => {
      destroyed = true;
      clearTimeout(retryRef.current);
      wsRef.current?.close();
    };
  }, [tenantId]);

  return { readings, status };
}
