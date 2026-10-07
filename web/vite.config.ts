import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import fs from "fs";
import { createHash } from "crypto";

// Backend for the dev proxy (override to run against another API, e.g. API_PROXY_TARGET=http://localhost:8101).
const API_TARGET = process.env.API_PROXY_TARGET ?? "http://localhost:8000";
const DEV_PORT = Number(process.env.DEV_PORT ?? 5173);

/**
 * Long-lived vendor chunks: they change far less often than the app code, so a deploy only invalidates the app
 * chunks in the browsers / service worker caches. Charts are their own chunk (only screens with charts load it).
 */
function vendorChunk(id: string): string | undefined {
  if (!id.includes("node_modules")) return undefined;
  if (/node_modules\/(react|react-dom|scheduler|react-router|react-router-dom|@remix-run)\//.test(id)) return "vendor-react";
  if (/node_modules\/(@tanstack|axios|zustand|clsx)\//.test(id)) return "vendor-data";
  if (/node_modules\/(recharts|recharts-scale|react-smooth|d3-[a-z-]+|internmap|decimal\.js-light|victory-vendor)\//.test(id)) return "vendor-charts";
  return undefined;
}

/**
 * Writes /asset-manifest.json with the hashed chunks of this build. The service worker (public/sw.js) precaches
 * them on install — so the technician app and the operator tablet open every screen offline, not only the ones
 * visited before losing signal — and drops the chunks of previous deploys from its cache. hls.js (cameras, never
 * offline) is left out.
 */
function assetManifest(): Plugin {
  let outDir = "dist";
  let buildId = "dev";
  return {
    name: "fm-asset-manifest",
    apply: "build",
    configResolved(c) { outDir = path.resolve(c.root, c.build.outDir); },
    generateBundle(_opts, bundle) {
      const files = Object.values(bundle)
        .map((f) => f.fileName)
        .filter((f) => f.startsWith("assets/") && /\.(js|css|woff2)$/.test(f) && !/hls|cyrillic|greek|vietnamese/.test(f))
        .sort();
      buildId = createHash("sha256").update(files.join("\n")).digest("hex").slice(0, 12);
      this.emitFile({ type: "asset", fileName: "asset-manifest.json", source: JSON.stringify({ build: buildId, files: files.map((f) => `/${f}`) }) });
    },
    // sw.js is copied verbatim from public/: stamp the build id so every deploy changes its bytes and the browser
    // installs the new worker (which precaches the new chunks and prunes the old ones).
    closeBundle() {
      const sw = path.join(outDir, "sw.js");
      if (fs.existsSync(sw)) fs.writeFileSync(sw, fs.readFileSync(sw, "utf8").replace("__FM_BUILD_ID__", buildId));
    },
  };
}

/**
 * <link rel="preload"> for the Inter faces of the first paint: without it the text first renders in the fallback font
 * and re-flows when Inter arrives (labels that wrap differently = layout shift, CLS).
 */
function preloadFonts(): Plugin {
  return {
    name: "fm-preload-fonts",
    apply: "build",
    transformIndexHtml: {
      order: "post",
      handler(_html, ctx) {
        const files = Object.keys(ctx.bundle ?? {}).filter((f) => /assets\/inter-latin-(400|600)-normal-[^/]+\.woff2$/.test(f));
        return files.map((f) => ({ tag: "link", attrs: { rel: "preload", href: `/${f}`, as: "font", type: "font/woff2", crossorigin: "" }, injectTo: "head" as const }));
      },
    },
  };
}

export default defineConfig({
  plugins: [react(), assetManifest(), preloadFonts()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  build: {
    // hls.js (light, ~350 kB) is the largest chunk and is only fetched when a camera stream is opened
    chunkSizeWarningLimit: 500,
    rollupOptions: {
      output: { manualChunks: vendorChunk },
    },
  },
  server: {
    host: true,
    port: DEV_PORT,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true },
      "/ws": { target: API_TARGET.replace(/^http/, "ws"), ws: true },
    },
  },
});
