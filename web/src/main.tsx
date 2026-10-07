import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
// self-hosted fonts: works on plant networks without internet access
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "./index.css";
import { watchSystemTheme } from "./store/theme";

watchSystemTheme();

// A tab opened before a deploy asks for a chunk the server no longer has (hashed names change): reload once to get
// the new build instead of showing a broken screen. Guarded so a real outage does not loop.
window.addEventListener("vite:preloadError", (event) => {
  try {
    const last = Number(sessionStorage.getItem("fm-chunk-reload") ?? 0);
    if (Date.now() - last < 30_000) return;
    sessionStorage.setItem("fm-chunk-reload", String(Date.now()));
  } catch { /* storage blocked: still try once */ }
  event.preventDefault();
  window.location.reload();
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
