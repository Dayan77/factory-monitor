// Comportamento do botão "Ver demonstração" (componente DemoButton.astro; regras em src/lib/demo.ts).
import { MSG_LOADING, checkDemo, trackDemoOpened } from "./demo";

const RETRY = "Tentar de novo";

document.querySelectorAll<HTMLAnchorElement>("a[data-demo-cta]").forEach((a) => {
  const status = a.parentElement?.querySelector<HTMLElement>("[data-demo-status]") ?? null;
  const label = a.querySelector<HTMLElement>("[data-demo-label]");
  const spinner = a.querySelector<SVGElement>("[data-demo-spinner]");
  let busy = false;
  const initial = label?.textContent ?? "Ver demonstração";
  const loading = a.dataset.demoLoading || MSG_LOADING;

  const setBusy = (on: boolean) => {
    busy = on;
    if (on) a.setAttribute("aria-busy", "true"); else a.removeAttribute("aria-busy");
    spinner?.classList.toggle("hidden", !on);
    if (label && on) label.textContent = loading;
  };

  a.addEventListener("click", async (ev) => {
    // nova aba / janela (Ctrl, Cmd, Shift, botão do meio): deixa o navegador abrir o link direto
    if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    ev.preventDefault();
    if (busy) return;
    setBusy(true);
    if (status) { status.textContent = ""; delete status.dataset.state; }
    const result = await checkDemo(a.dataset.demoStatusUrl || "", (u, init) => fetch(u, init));
    if (result.kind === "ok") {
      trackDemoOpened(a.dataset.demoEventUrl || "", a.dataset.demoCta || "outro",
                      navigator as Navigator & { globalPrivacyControl?: boolean });
      window.location.assign(a.href);
      return;          // a página vai mudar: mantém o "carregando"
    }
    setBusy(false);
    if (label) label.textContent = RETRY;
    if (status) {
      status.textContent = result.message;
      status.dataset.state = "error";
    }
  });

  // voltar do app pelo histórico (bfcache): botão no estado inicial
  window.addEventListener("pageshow", () => {
    setBusy(false);
    if (label) label.textContent = initial;
  });
});
