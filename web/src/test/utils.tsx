import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import type { AxiosResponse } from "axios";

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity, refetchInterval: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

export function renderWithClient(ui: ReactElement, client: QueryClient = makeQueryClient()) {
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>{ui}</MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

/** Minimal AxiosResponse for mocked API calls. */
export function ok<T>(data: T): AxiosResponse<T> {
  return { data, status: 200, statusText: "OK", headers: {}, config: { headers: {} } } as AxiosResponse<T>;
}

/** A promise that never settles — keeps a query in its loading state. */
export function pending<T>(): Promise<T> {
  return new Promise<T>(() => {});
}

/** Renders `ui` at `path`, matched by the route `pattern` (for pages that use useParams). */
export function renderRoute(ui: ReactElement, path: string, pattern: string, client: QueryClient = makeQueryClient()) {
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <Routes>
            <Route path={pattern} element={<>{ui}<LocationProbe /></>} />
            <Route path="*" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

/** Exposes the current location for assertions (data-testid="location"). */
export function LocationProbe() {
  const loc = useLocation();
  return <span data-testid="location" hidden>{loc.pathname + loc.search}</span>;
}
