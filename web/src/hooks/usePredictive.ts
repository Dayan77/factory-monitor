/**
 * React Query hooks of the predictive screens (Sprint 5). Key family: ["predictive", …].
 * Any mutation invalidates ["predictive"]; conversion also invalidates ["maintenance"] (a WO was born).
 * The realtime socket ({type: 'predictive_alert'}) invalidates ["predictive"] too (useWebSocket.ts).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { predictiveApi } from "@/services/predictiveApi";
import type {
  AlertQuery, ConvertAlertBody, FeatureQuery, MonitorCreateBody, MonitorPatchBody, MonitorQuery, RuleCreateBody,
  RulePatchBody, TransitionAlertBody,
} from "@/services/predictiveTypes";

export const predKeys = {
  all: ["predictive"] as const,
  monitors: (q: MonitorQuery) => ["predictive", "monitors", q] as const,
  monitor: (id: string) => ["predictive", "monitor", id] as const,
  features: (q: FeatureQuery) => ["predictive", "features", q] as const,
  alerts: (q: AlertQuery) => ["predictive", "alerts", q] as const,
  alert: (id: string) => ["predictive", "alert", id] as const,
  evaluation: (q: object) => ["predictive", "evaluation", q] as const,
};

export const OPEN_STATES = ["open", "acknowledged", "converted"] as const;

export function useMonitors(q: MonitorQuery = {}, enabled = true) {
  return useQuery({
    queryKey: predKeys.monitors(q),
    queryFn: () => predictiveApi.monitors(q).then((r) => r.data),
    refetchInterval: 60_000,
    enabled,
    placeholderData: (prev) => prev,
  });
}

export function useMonitor(id: string | null) {
  return useQuery({
    queryKey: predKeys.monitor(id ?? ""),
    queryFn: () => predictiveApi.monitor(id as string).then((r) => r.data),
    enabled: !!id,
  });
}

export function useFeatures(q: FeatureQuery, enabled = true) {
  return useQuery({
    queryKey: predKeys.features(q),
    queryFn: () => predictiveApi.features(q).then((r) => r.data),
    enabled: enabled && !!(q.position_id || q.monitor_id),
    refetchInterval: 120_000,
    placeholderData: (prev) => prev,
  });
}

export function useAlerts(q: AlertQuery = {}, enabled = true) {
  return useQuery({
    queryKey: predKeys.alerts(q),
    queryFn: () => predictiveApi.alerts(q).then((r) => r.data),
    refetchInterval: 60_000,
    enabled,
    placeholderData: (prev) => prev,
  });
}

export function useAlert(id: string | null) {
  return useQuery({
    queryKey: predKeys.alert(id ?? ""),
    queryFn: () => predictiveApi.alert(id as string).then((r) => r.data),
    enabled: !!id,
  });
}

export function useEvaluation(q: { start?: string; stop?: string; line_id?: string }, enabled = true) {
  return useQuery({
    queryKey: predKeys.evaluation(q),
    queryFn: () => predictiveApi.evaluation(q).then((r) => r.data),
    enabled,
  });
}

function useInvalidate() {
  const qc = useQueryClient();
  return () => Promise.all([
    qc.invalidateQueries({ queryKey: predKeys.all }),
  ]);
}

export function useTransitionAlert() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: TransitionAlertBody }) => predictiveApi.transition(id, body).then((r) => r.data),
    onSuccess: invalidate,
  });
}

export function useConvertAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body?: ConvertAlertBody }) => predictiveApi.convert(id, body).then((r) => r.data),
    onSuccess: () => Promise.all([
      qc.invalidateQueries({ queryKey: predKeys.all }),
      qc.invalidateQueries({ queryKey: ["maintenance"] }),
    ]),
  });
}

export function useSaveMonitor() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (a: { id: string; body: MonitorPatchBody } | { id?: undefined; body: MonitorCreateBody }) =>
      (a.id ? predictiveApi.updateMonitor(a.id, a.body) : predictiveApi.createMonitor(a.body as MonitorCreateBody)).then((r) => r.data),
    onSuccess: invalidate,
  });
}

export function useRebaseline() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => predictiveApi.rebaseline(id, reason).then((r) => r.data),
    onSuccess: invalidate,
  });
}

export function useSaveRule() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (a: { id: string; body: RulePatchBody } | { id?: undefined; body: RuleCreateBody }) =>
      (a.id ? predictiveApi.updateRule(a.id, a.body) : predictiveApi.createRule(a.body as RuleCreateBody)).then((r) => r.data),
    onSuccess: invalidate,
  });
}

export function useDeleteRule() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: (id: string) => predictiveApi.deleteRule(id), onSuccess: invalidate });
}
