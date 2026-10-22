/** @type {import('tailwindcss').Config} */

// Every color is a CSS variable (src/styles/tokens.css) so light/dark themes switch at runtime.
const STEPS = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
const palette = (name) => Object.fromEntries(STEPS.map((s) => [s, v(`${name}-${s}`)]));

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        gray: palette("gray"),
        brand: palette("brand"),
        red: palette("red"),
        yellow: palette("yellow"),
        amber: palette("amber"),
        green: palette("green"),
        blue: palette("blue"),
        purple: palette("purple"),
        // semantic tokens
        canvas: v("c-canvas"),
        surface: { DEFAULT: v("c-surface"), 2: v("c-surface-2") },
        line: { DEFAULT: v("c-line"), strong: v("c-line-strong") },
        fg: { DEFAULT: v("c-fg"), muted: v("c-fg-muted"), subtle: v("c-fg-subtle") },
        accent: { DEFAULT: v("c-accent"), hover: v("c-accent-hover"), fg: v("c-on-accent") },
        ok: { DEFAULT: v("c-ok"), solid: v("c-ok-solid") },
        warn: { DEFAULT: v("c-warn"), solid: v("c-warn-solid") },
        danger: { DEFAULT: v("c-danger"), solid: v("c-danger-solid") },
        info: { DEFAULT: v("c-info"), solid: v("c-info-solid") },
        neutral: { DEFAULT: v("c-neutral"), solid: v("c-neutral-solid") },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
      // Typography scale (rem / line-height / weight)
      fontSize: {
        overline: ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.06em", fontWeight: "600" }],
        caption: ["0.75rem", { lineHeight: "1rem" }],
        body: ["0.875rem", { lineHeight: "1.375rem" }],
        heading: ["1rem", { lineHeight: "1.5rem", fontWeight: "600" }],
        title: ["1.25rem", { lineHeight: "1.75rem", fontWeight: "600", letterSpacing: "-0.01em" }],
        display: ["1.875rem", { lineHeight: "2.25rem", fontWeight: "700", letterSpacing: "-0.02em" }],
      },
      boxShadow: {
        card: "var(--shadow-card)",
        pop: "var(--shadow-pop)",
      },
      transitionTimingFunction: {
        "out-soft": "cubic-bezier(0.22, 1, 0.36, 1)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "slide-in-right": { from: { transform: "translateX(100%)" }, to: { transform: "translateX(0)" } },
        "slide-in-up": { from: { transform: "translateY(100%)" }, to: { transform: "translateY(0)" } },
        "rise-in": { from: { opacity: "0", transform: "translateY(4px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        flash: { "0%": { backgroundColor: "rgb(var(--c-ok) / 0.25)" }, "100%": { backgroundColor: "transparent" } },
        // plant map: a marker that changed state pulses (sprint6 §1.3)
        "marker-pulse": {
          "0%": { boxShadow: "0 0 0 0 rgb(var(--c-fg) / 0.55)" },
          "70%": { boxShadow: "0 0 0 14px rgb(var(--c-fg) / 0)" },
          "100%": { boxShadow: "0 0 0 0 rgb(var(--c-fg) / 0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 160ms ease-out",
        "slide-in-right": "slide-in-right 240ms cubic-bezier(0.22, 1, 0.36, 1)",
        "slide-in-up": "slide-in-up 260ms cubic-bezier(0.22, 1, 0.36, 1)",
        "rise-in": "rise-in 220ms cubic-bezier(0.22, 1, 0.36, 1)",
        flash: "flash 900ms ease-out",
        "marker-pulse": "marker-pulse 1.1s ease-out 3",
      },
    },
  },
  plugins: [],
};
