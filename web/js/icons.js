// Small inline stroke icons (24×24 grid, currentColor), standing in for the SF Symbols the iPhone app uses.
import { html } from "../vendor/preact-htm.js";

const P = {
  plus: "M12 5v14M5 12h14",
  check: "M5 12.5l4.5 4.5L19 7",
  "check-circle": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM8 12.5l2.8 2.8L16.5 9.5",
  x: "M6 6l12 12M18 6L6 18",
  "x-octagon": "M8 3h8l5 5v8l-5 5H8l-5-5V8zM9.5 9.5l5 5M14.5 9.5l-5 5",
  retry: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4",
  download: "M12 4v11M7 10.5l5 5 5-5M5 20h14",
  upload: "M12 20V9M7 13.5l5-5 5 5M5 4h14",
  calendar: "M4 6h16v14H4zM4 10h16M8 3v5M16 3v5M8 14h2M14 14h2M8 17h2",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7.5V12l3 2",
  pause: "M9 6v12M15 6v12",
  alert: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7.5v5.5M12 16.2v.3",
  warning: "M12 3.5L2.5 20h19zM12 10v4.5M12 17.2v.3",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v5.5M12 7.7v.3",
  question: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6M12 16.7v.3",
  tray: "M4 13l2.5-7h11l2.5 7v6H4zM4 13h4.5l1 2h5l1-2H20",
  film: "M4 4h16v16H4zM8 4v16M16 4v16M4 8h4M4 12h4M4 16h4M16 8h4M16 12h4M16 16h4",
  tv: "M3 6h18v12H3zM8 21h8M9 2.5l3 3.5 3-3.5",
  sparkles: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z",
  activity: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7.5v8M8.5 12.5L12 16l3.5-3.5",
  gear: "M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4zM19.4 13.5l1.6 1.2-1.8 3.1-1.9-.7a7 7 0 0 1-1.7 1l-.3 2h-3.6l-.3-2a7 7 0 0 1-1.7-1l-1.9.7-1.8-3.1 1.6-1.2a7 7 0 0 1 0-2l-1.6-1.3 1.8-3.1 1.9.8a7 7 0 0 1 1.7-1l.3-2h3.6l.3 2a7 7 0 0 1 1.7 1l1.9-.8 1.8 3.1-1.6 1.3a7 7 0 0 1 0 2z",
  filter: "M4 6h16M7 12h10M10 18h4",
  sort: "M7 4v16M3.5 8L7 4l3.5 4M17 20V4M13.5 16l3.5 4 3.5-4",
  back: "M15 5l-7 7 7 7",
  chevron: "M9 5l7 7-7 7",
  sliders: "M4 7h10M18 7h2M4 17h2M10 17h10M16 5v4M8 15v4",
  stethoscope: "M6 3v6a4 4 0 0 0 8 0V3M10 13v2a5 5 0 0 0 10 0v-2M20 11.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z",
  bug: "M8 9a4 4 0 0 1 8 0v6a4 4 0 0 1-8 0zM12 9v10M4 12h4M16 12h4M5 7l3 2M19 7l-3 2M5 18l3-2M19 18l-3-2",
  offline: "M3 3l18 18M8.5 16.5a5 5 0 0 1 7 0M5 13a10 10 0 0 1 5-2.7M19 13a10 10 0 0 0-2.3-1.6M2 9.5a15 15 0 0 1 4.3-2.8M22 9.5A15 15 0 0 0 11 5.6M12 20h.01",
  popcorn: "M6 9l1.5 12h9L18 9zM5 9a3 3 0 0 1 2.5-4 3.5 3.5 0 0 1 4.5-2 3.5 3.5 0 0 1 4.5 2A3 3 0 0 1 19 9z",
  share: "M12 3v12M7.5 7.5L12 3l4.5 4.5M5 12v8h14v-8",
  circle: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
  more: "M6 12h.01M12 12h.01M18 12h.01",
  changes: "M4 9a8 8 0 0 1 14-3l2 2M20 15a8 8 0 0 1-14 3l-2-2M20 4v4h-4M4 20v-4h4",
};

export function Icon({ name, size = 20, stroke = 2, cls = "" }) {
  return html`<svg class=${`icon ${cls}`} width=${size} height=${size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width=${stroke} stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d=${P[name] || P.question} />
  </svg>`;
}
