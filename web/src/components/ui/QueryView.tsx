import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import ErrorState from "./ErrorState";

interface Props<T> {
  query: UseQueryResult<T>;
  /** Skeleton shown on first load. */
  loading: ReactNode;
  errorTitle?: string;
  /** Rendered when `isEmpty(data)` is true. */
  empty?: ReactNode;
  isEmpty?: (data: T) => boolean;
  children: (data: T) => ReactNode;
}

/**
 * Explicit loading / error / empty / data states for a React Query result.
 * Never falls back to fake data. If a background refetch fails while we
 * still have data, the (stale) data keeps being shown.
 */
export default function QueryView<T>({ query, loading, errorTitle, empty, isEmpty, children }: Props<T>) {
  if (query.isPending) return <>{loading}</>;
  if (query.isError) {
    return <ErrorState title={errorTitle} error={query.error} onRetry={() => void query.refetch()} />;
  }
  if (empty !== undefined && isEmpty?.(query.data)) return <>{empty}</>;
  return <>{children(query.data)}</>;
}
