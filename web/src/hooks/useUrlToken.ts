import { useEffect, useState } from "react";

/**
 * Reads a one-time credential from `?token=` and, right after the first render, removes it from the address bar so
 * it does not stay in the browser history, in screenshots or in the Referer of the next request. (Reading stays pure:
 * StrictMode calls state initializers twice.)
 */
export function useUrlToken(param = "token"): string {
  const [token] = useState(() => new URL(window.location.href).searchParams.get(param) ?? "");
  useEffect(() => {
    const url = new URL(window.location.href);
    if (!url.searchParams.has(param)) return;
    url.searchParams.delete(param);
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  }, [param]);
  return token;
}
