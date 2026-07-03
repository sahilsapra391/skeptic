import type { Config } from "tailwindcss";

/**
 * Token system from the approved design (docs/design/Skeptic App.dc.html).
 *
 * COLOR CONTRACT (TECH-SPEC §8): the trust/verdict hue family (`trust*`)
 * and the P/L pair (`pl-pos`/`pl-neg`) are separate token families. Trust
 * components never use pl-* tokens and P/L data never uses trust tokens.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ground: "#14161a", // page background
        navbg: "#15181d",
        panel: "#1b1f26",
        "panel-deep": "#181b21", // trade-log body
        "panel-chart": "#171a20", // chart-teach canvas
        raised: "#20242d", // quote bubbles / tickets
        "raised-2": "#242a34", // disabled buttons
        "raised-3": "#262c36", // active mode toggle
        line: "#2b303a",
        "line-soft": "#262c36",
        "line-softer": "#232833",
        "line-hover": "#3a4150",
        grid: "#20242c", // chart gridlines
        "band-track": "#2e3440", // trust-band rail
        ink: "#e9edf1",
        "ink-2": "#c6cdd6",
        "ink-3": "#98a2ad",
        "ink-4": "#5f6873",
        "ink-5": "#4a545f",
        chart: "#cdd6df",
        "chart-bright": "#d7dde3",
        warn: "#d9a441",
        // trust hue family — verdict/trust surfaces ONLY
        trust: "var(--ac)",
        "trust-dim": "var(--acd)",
        "trust-border": "var(--acb)",
        // P/L pair — profit/loss data ONLY
        "pl-pos": "#43c987",
        "pl-neg": "#e0604f",
      },
      fontFamily: {
        sans: ["var(--font-archivo)", "sans-serif"],
        mono: ["var(--font-plex-mono)", "monospace"],
      },
      maxWidth: {
        shell: "1620px",
      },
    },
  },
  plugins: [],
};

export default config;
