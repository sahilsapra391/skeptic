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
        ground: "var(--ground)",
        navbg: "var(--navbg)",
        panel: "var(--panel)",
        "panel-deep": "var(--panel-deep)",
        "panel-chart": "var(--panel-chart)",
        raised: "var(--raised)",
        "raised-2": "var(--raised-2)",
        "raised-3": "var(--raised-3)",
        line: "var(--line)",
        "line-soft": "var(--line-soft)",
        "line-softer": "var(--line-softer)",
        "line-hover": "var(--line-hover)",
        grid: "var(--grid)",
        "band-track": "var(--band-track)",
        ink: "var(--ink)",
        "ink-2": "var(--ink-2)",
        "ink-3": "var(--ink-3)",
        "ink-4": "var(--ink-4)",
        "ink-5": "var(--ink-5)",
        chart: "var(--chart)",
        "chart-bright": "var(--chart-bright)",
        warn: "rgb(var(--warn-rgb) / <alpha-value>)",
        // trust hue family — verdict/trust surfaces ONLY
        trust: "rgb(var(--ac-rgb) / <alpha-value>)",
        "trust-dim": "var(--acd)",
        "trust-border": "var(--acb)",
        "trust-faint": "var(--ac-faint)",
        // P/L pair — profit/loss data ONLY
        "pl-pos": "var(--pl-pos)",
        "pl-neg": "var(--pl-neg)",
        "on-accent": "var(--on-accent)",
      },
      // three-voice type system (owner directive 2026-07-03): Archivo for
      // body, Plex Mono for data, Newsreader serif for headings/important
      // moments only — no other families
      fontFamily: {
        sans: ["var(--font-archivo)", "sans-serif"],
        mono: ["var(--font-plex-mono)", "monospace"],
        serif: ["var(--font-newsreader)", "Georgia", "serif"],
      },
      maxWidth: {
        shell: "1620px",
      },
      boxShadow: {
        soft: "var(--shadow-soft)",
        pop: "var(--shadow-pop)",
      },
    },
  },
  plugins: [],
};

export default config;
