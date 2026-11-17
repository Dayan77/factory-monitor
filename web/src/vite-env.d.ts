/// <reference types="vite/client" />

// hls.js ships the light build without its own .d.ts: same API as the full build.
declare module "hls.js/light" {
  import Hls from "hls.js";
  export default Hls;
}

interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
  /** Public site (manual / docs links from the KPI tooltips). Default https://factoryconnect.tech */
  readonly VITE_SITE_URL?: string;
  /** Edge image of the gateway install command. Default ghcr.io/dayan77/factoryconnect-edge:0.6.0 */
  readonly VITE_EDGE_IMAGE?: string;
}
