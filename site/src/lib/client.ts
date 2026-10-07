// Comportamentos globais leves: tema, menu mobile, animação de entrada e o botão "Ver demonstração".
import "./demo-client";

const root = document.documentElement;

function setTheme(theme: "light" | "dark") {
  root.classList.toggle("dark", theme === "dark");
  try { localStorage.setItem("fc-theme", theme); } catch { /* storage indisponível */ }
  document.querySelectorAll<HTMLButtonElement>("[data-theme-toggle]").forEach((b) => {
    b.setAttribute("aria-pressed", String(theme === "light"));
  });
}

document.querySelectorAll<HTMLButtonElement>("[data-theme-toggle]").forEach((btn) => {
  btn.setAttribute("aria-pressed", String(!root.classList.contains("dark")));
  btn.addEventListener("click", () => setTheme(root.classList.contains("dark") ? "light" : "dark"));
});

const menuBtn = document.querySelector<HTMLButtonElement>("[data-menu-toggle]");
const menu = document.getElementById("menu-mobile");
if (menuBtn && menu) {
  const close = () => {
    menu.hidden = true;
    menuBtn.setAttribute("aria-expanded", "false");
  };
  menuBtn.addEventListener("click", () => {
    const open = menu.hidden;
    menu.hidden = !open;
    menuBtn.setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !menu.hidden) { close(); menuBtn.focus(); }
  });
  window.matchMedia("(min-width: 1024px)").addEventListener("change", (e) => { if (e.matches) close(); });
}

const reveals = document.querySelectorAll<HTMLElement>(".reveal");
if ("IntersectionObserver" in window) {
  const io = new IntersectionObserver(
    (entries) => {
      for (const en of entries) {
        if (en.isIntersecting) { en.target.classList.add("is-visible"); io.unobserve(en.target); }
      }
    },
    { rootMargin: "0px 0px -8% 0px", threshold: 0.05 },
  );
  reveals.forEach((el) => io.observe(el));
} else {
  reveals.forEach((el) => el.classList.add("is-visible"));
}
