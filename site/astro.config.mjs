// @ts-check
import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";
import tailwindcss from "@tailwindcss/vite";

// Site público (porta de entrada) — o app fica em app.factoryconnect.tech.
export default defineConfig({
  site: "https://factoryconnect.tech",
  trailingSlash: "never",
  build: { format: "file", inlineStylesheets: "always" },
  compressHTML: true,
  integrations: [
    sitemap({
      filter: (page) => !/\/404\/?$/.test(page),
    }),
  ],
  vite: { plugins: [tailwindcss()] },
});
