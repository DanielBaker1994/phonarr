// Builds and saves the debug report (Settings → Troubleshooting, and the "screen crashed" panel).
// The gateway zips its logs and status with this browser's log (POST /v1/debug/bundle).
import { api } from "./api.js";
import { log } from "./log.js";
import * as S from "./store.js";
import { state } from "./store.js";

export async function downloadDebugReport(extra = {}) {
  log.info("debug_report");
  const info = {
    frontend: "web",
    user_agent: navigator.userAgent,
    standalone: matchMedia("(display-mode: standalone)").matches || navigator.standalone === true,
    secure_context: isSecureContext,
    service_worker: !!navigator.serviceWorker?.controller,
    viewport: `${innerWidth}x${innerHeight}@${devicePixelRatio}`,
    gateway_host: location.host,
    gateway_reachable: state.gatewayReachable,
    radarr_configured: !!state.config?.radarr?.configured,
    sonarr_configured: !!state.config?.sonarr?.configured,
    services: state.services,
    tracked_titles: S.trackedCount(),
    generated_at: new Date().toISOString(),
    ...extra,
  };
  const blob = await api.debugBundle(log.text(), info);
  const fileName = `Phonarr-debug-${new Date().toISOString().slice(0, 19).replace(/:/g, "")}.zip`;
  const file = new File([blob], fileName, { type: "application/zip" });
  if (navigator.canShare?.({ files: [file] })) {
    await navigator.share({ files: [file], title: "Phonarr debug report" }).catch(() => {});
  } else {
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: fileName });
    document.body.append(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }
}
