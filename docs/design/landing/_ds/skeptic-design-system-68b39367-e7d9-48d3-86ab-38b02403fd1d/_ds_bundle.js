/* @ds-bundle: {"format":4,"namespace":"SkepticDesignSystem_68b393","components":[{"name":"Composer","sourcePath":"components/composer/Composer.jsx"},{"name":"CoverageChip","sourcePath":"components/composer/CoverageChip.jsx"},{"name":"PresetChip","sourcePath":"components/composer/PresetChip.jsx"},{"name":"QuestionCard","sourcePath":"components/composer/QuestionCard.jsx"},{"name":"ThinkingIndicator","sourcePath":"components/composer/ThinkingIndicator.jsx"},{"name":"Button","sourcePath":"components/core/Button.jsx"},{"name":"DemoBadge","sourcePath":"components/core/DemoBadge.jsx"},{"name":"Disclaimer","sourcePath":"components/core/Disclaimer.jsx"},{"name":"Hint","sourcePath":"components/core/Hint.jsx"},{"name":"MetricTile","sourcePath":"components/core/MetricTile.jsx"},{"name":"Panel","sourcePath":"components/core/Panel.jsx"},{"name":"PulsingDots","sourcePath":"components/core/PulsingDots.jsx"},{"name":"GauntletProgress","sourcePath":"components/gauntlet/GauntletProgress.jsx"},{"name":"NavRail","sourcePath":"components/navigation/NavRail.jsx"},{"name":"TrustBand","sourcePath":"components/verdict/TrustBand.jsx"},{"name":"VerdictBlock","sourcePath":"components/verdict/VerdictBlock.jsx"}],"sourceHashes":{"components/composer/Composer.jsx":"c30cadc26b4b","components/composer/CoverageChip.jsx":"b0c683e27676","components/composer/PresetChip.jsx":"8aa3e418c055","components/composer/QuestionCard.jsx":"9b8731e60fe9","components/composer/ThinkingIndicator.jsx":"704ae0e77cbd","components/core/Button.jsx":"01f391d3c7b5","components/core/DemoBadge.jsx":"55a0c7c51b71","components/core/Disclaimer.jsx":"b2565862b648","components/core/Hint.jsx":"6fb3397e9e01","components/core/MetricTile.jsx":"88f8f7267d1b","components/core/Panel.jsx":"47732dfb9fcc","components/core/PulsingDots.jsx":"86470c16040a","components/gauntlet/GauntletProgress.jsx":"ce8d01e1d2f1","components/navigation/NavRail.jsx":"c53bc3c2a5e5","components/verdict/TrustBand.jsx":"a7d777292fca","components/verdict/VerdictBlock.jsx":"8b97579e1813","ui_kits/app/App.jsx":"e858f7f4a16e","ui_kits/app/HomeScreen.jsx":"48917431eea2","ui_kits/app/LibraryScreen.jsx":"1112b53c4e2d","ui_kits/app/ResultsScreen.jsx":"87baef73adf8","ui_kits/app/SettingsScreen.jsx":"b3a4bfd092ed","ui_kits/app/demo-data.js":"0819c3f8ffab"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.SkepticDesignSystem_68b393 = window.SkepticDesignSystem_68b393 || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/composer/CoverageChip.jsx
try { (() => {
/** The honest data-coverage chip — per-ticker, asymmetric on purpose.
 * fill 0–1 paints the meter (trust hue when thin, ink when deep).
 * state: "ok" | "loading" | "warn". */
function CoverageChip({
  label,
  fill = 0,
  range,
  state = "ok"
}) {
  if (state === "loading") {
    return /*#__PURE__*/React.createElement("span", {
      className: "animate-pin-pulse",
      style: {
        display: "inline-flex",
        alignItems: "center",
        borderRadius: 999,
        border: "1px solid var(--line-soft)",
        padding: "5px 12px",
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        color: "var(--ink-4)"
      }
    }, "reading the lake\u2026");
  }
  if (state === "warn") {
    return /*#__PURE__*/React.createElement("span", {
      style: {
        display: "inline-flex",
        alignItems: "center",
        borderRadius: 999,
        border: "1px solid rgb(var(--warn-rgb) / .5)",
        padding: "5px 12px",
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        color: "var(--warn)"
      }
    }, label);
  }
  return /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: 7,
      borderRadius: 999,
      border: "1px solid var(--line-soft)",
      padding: "5px 12px",
      fontFamily: "var(--font-mono)",
      fontSize: 11,
      color: "var(--ink-3)"
    }
  }, label, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-block",
      height: 5,
      width: 36,
      overflow: "hidden",
      borderRadius: 3,
      background: "var(--line-soft)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "block",
      height: "100%",
      width: Math.max(3, Math.round(fill * 100)) + "%",
      background: fill > 0.5 ? "var(--ink-4)" : "var(--ac)"
    }
  })), range);
}
Object.assign(__ds_scope, { CoverageChip });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/composer/CoverageChip.jsx", error: String((e && e.message) || e) }); }

// components/composer/PresetChip.jsx
try { (() => {
/** Starter-strategy chip under the composer: label + mono structure line. */
function PresetChip({
  label,
  structure,
  phrase,
  onClick
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("button", {
    onClick: onClick,
    title: phrase,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      borderRadius: 14,
      border: "1px solid " + (hover ? "var(--line-hover)" : "var(--line-soft)"),
      background: hover ? "var(--raised)" : "var(--panel)",
      padding: "10px 16px",
      textAlign: "left"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 14,
      fontWeight: 500,
      color: hover ? "var(--ink)" : "var(--ink-2)"
    }
  }, label), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 2,
      fontFamily: "var(--font-mono)",
      fontSize: 11,
      letterSpacing: ".04em",
      color: hover ? "var(--ink-3)" : "var(--ink-4)"
    }
  }, structure));
}
Object.assign(__ds_scope, { PresetChip });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/composer/PresetChip.jsx", error: String((e && e.message) || e) }); }

// components/composer/QuestionCard.jsx
try { (() => {
/** Clarifying-question card — "I don't guess." Chips answer in one tap;
 * the input takes the user's own words. One question at a time. */
function QuestionCard({
  index = 1,
  total = 1,
  question,
  options = [],
  onAnswer,
  busy = false
}) {
  const [text, setText] = React.useState("");
  const submit = ans => {
    if (ans && ans.trim() && onAnswer) onAnswer(ans.trim());
  };
  return /*#__PURE__*/React.createElement("div", {
    style: {
      borderRadius: 14,
      border: "1px solid var(--acb)",
      background: "var(--acd)",
      padding: "16px 20px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 4,
      fontFamily: "var(--font-mono)",
      fontSize: 10.5,
      fontWeight: 500,
      letterSpacing: ".12em",
      color: "var(--ac)"
    }
  }, "QUESTION ", index, " OF ", total, " \u2014 I DON'T GUESS"), /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 14,
      fontSize: 16.5,
      fontWeight: 600,
      lineHeight: 1.375
    }
  }, question), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexWrap: "wrap",
      alignItems: "center",
      gap: 8
    }
  }, options.map(opt => /*#__PURE__*/React.createElement("button", {
    key: opt,
    onClick: () => submit(opt),
    disabled: busy,
    style: {
      borderRadius: 999,
      border: "1px solid var(--acb)",
      padding: "6px 14px",
      fontSize: 13,
      color: "var(--ac)"
    }
  }, opt))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 12,
      display: "flex",
      alignItems: "center",
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("input", {
    value: text,
    onChange: e => setText(e.target.value),
    placeholder: "or answer in your own words \u21B5",
    autoFocus: true,
    onKeyDown: e => {
      if (e.key === "Enter") {
        submit(text);
        setText("");
      }
    },
    style: {
      flex: 1,
      borderRadius: 9,
      border: "1px solid var(--line)",
      background: "var(--panel-deep)",
      padding: "8px 12px",
      fontFamily: "var(--font-mono)",
      fontSize: 13,
      color: "var(--ink)"
    }
  }), /*#__PURE__*/React.createElement("button", {
    onClick: () => {
      submit(text);
      setText("");
    },
    disabled: !text.trim() || busy,
    style: {
      borderRadius: 9,
      padding: "8px 16px",
      fontSize: 13,
      fontWeight: 600,
      background: text.trim() && !busy ? "var(--ac)" : "var(--raised-2)",
      color: text.trim() && !busy ? "var(--on-accent)" : "var(--ink-4)",
      cursor: text.trim() && !busy ? "pointer" : "not-allowed"
    }
  }, busy ? "compiling…" : "answer")));
}
Object.assign(__ds_scope, { QuestionCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/composer/QuestionCard.jsx", error: String((e && e.message) || e) }); }

// components/core/Button.jsx
try { (() => {
/** The observed button recipes, codified (intentional addition — the app
 * styles buttons inline per-instance; values copied verbatim).
 * primary: trust fill · dark: ink fill (composer send) · secondary: raised
 * bordered · ghost: text-only · pill: mono bordered chip. */
const RECIPES = {
  primary: {
    base: {
      background: "var(--ac)",
      color: "var(--on-accent)",
      fontWeight: 700,
      borderRadius: 10,
      padding: "10px 20px",
      fontSize: 14
    },
    hover: {
      opacity: 0.9
    }
  },
  dark: {
    base: {
      background: "var(--ink)",
      color: "var(--ground)",
      fontWeight: 600,
      borderRadius: 10,
      padding: "9px 18px",
      fontSize: 13
    },
    hover: {
      background: "var(--ink-2)"
    }
  },
  secondary: {
    base: {
      border: "1px solid var(--line)",
      background: "var(--raised-2)",
      color: "var(--ink-2)",
      fontWeight: 600,
      borderRadius: 10,
      padding: "8px 16px",
      fontSize: 13
    },
    hover: {
      border: "1px solid var(--acb)",
      background: "var(--raised-3)",
      color: "var(--ink)"
    }
  },
  ghost: {
    base: {
      color: "var(--ink-4)",
      fontSize: 12.5,
      padding: "2px 0"
    },
    hover: {
      color: "var(--ink-3)"
    }
  },
  pill: {
    base: {
      border: "1px solid var(--line)",
      borderRadius: 999,
      padding: "6px 16px",
      fontFamily: "var(--font-mono)",
      fontSize: 11.5,
      color: "var(--ink-3)"
    },
    hover: {
      border: "1px solid var(--acb)",
      color: "var(--ac)"
    }
  },
  trustpill: {
    base: {
      border: "1px solid var(--acb)",
      borderRadius: 999,
      padding: "6px 14px",
      fontSize: 13,
      color: "var(--ac)"
    },
    hover: {
      background: "var(--acd)"
    }
  }
};
function Button({
  variant = "primary",
  disabled = false,
  children,
  onClick,
  style,
  title
}) {
  const [hover, setHover] = React.useState(false);
  const r = RECIPES[variant] || RECIPES.primary;
  const disabledStyle = disabled ? {
    background: "var(--raised-2)",
    color: "var(--ink-4)",
    cursor: "not-allowed",
    border: "none"
  } : null;
  return /*#__PURE__*/React.createElement("button", {
    title: title,
    disabled: disabled,
    onClick: onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: Object.assign({}, r.base, hover && !disabled ? r.hover : null, disabledStyle, style)
  }, children);
}
Object.assign(__ds_scope, { Button });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Button.jsx", error: String((e && e.message) || e) }); }

// components/core/DemoBadge.jsx
try { (() => {
/** Amber warn chip — marks demo/fixture content or a caveat. Never P/L. */
function DemoBadge({
  text = "demo data — engine lands at M2"
}) {
  return /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-flex",
      alignItems: "center",
      borderRadius: 999,
      border: "1px solid rgb(var(--warn-rgb) / .5)",
      padding: "4px 10px",
      fontFamily: "var(--font-mono)",
      fontSize: 10,
      letterSpacing: ".08em",
      color: "var(--warn)"
    }
  }, text);
}
Object.assign(__ds_scope, { DemoBadge });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/DemoBadge.jsx", error: String((e && e.message) || e) }); }

// components/core/Disclaimer.jsx
try { (() => {
/** Standing disclaimer — required on every results surface. */
function Disclaimer({
  short = false
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 12,
      textAlign: "center",
      fontSize: 11,
      color: "var(--ink-4)"
    }
  }, short ? "Research tool, not financial advice." : "Research tool, not financial advice. Backtests run on approximate self-collected data and overstate live results.");
}
Object.assign(__ds_scope, { Disclaimer });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Disclaimer.jsx", error: String((e && e.message) || e) }); }

// components/core/Hint.jsx
try { (() => {
/** Question-mark tooltip: plain-English explanations for stats and dials. */
function Hint({
  text,
  align = "center"
}) {
  const [show, setShow] = React.useState(false);
  return /*#__PURE__*/React.createElement("span", {
    style: {
      position: "relative",
      display: "inline-flex",
      flexShrink: 0
    },
    onMouseEnter: () => setShow(true),
    onMouseLeave: () => setShow(false)
  }, /*#__PURE__*/React.createElement("span", {
    "aria-label": text,
    style: {
      display: "flex",
      height: 15,
      width: 15,
      cursor: "help",
      userSelect: "none",
      alignItems: "center",
      justifyContent: "center",
      borderRadius: "50%",
      border: "1px solid " + (show ? "var(--line-hover)" : "var(--line)"),
      fontSize: 9.5,
      fontWeight: 600,
      lineHeight: 1,
      color: show ? "var(--ink-2)" : "var(--ink-4)",
      transition: "color .1s, border-color .1s"
    }
  }, "?"), /*#__PURE__*/React.createElement("span", {
    style: {
      pointerEvents: "none",
      position: "absolute",
      top: "calc(100% + 7px)",
      zIndex: 30,
      width: 230,
      borderRadius: 9,
      border: "1px solid var(--line)",
      background: "var(--raised)",
      padding: "8px 12px",
      textAlign: "left",
      fontFamily: "var(--font-sans)",
      fontSize: 11.5,
      fontWeight: 400,
      lineHeight: 1.55,
      letterSpacing: "normal",
      textTransform: "none",
      color: "var(--ink-2)",
      opacity: show ? 1 : 0,
      boxShadow: "var(--shadow-pop)",
      transition: "opacity .1s",
      left: align === "center" ? "50%" : "auto",
      right: align === "right" ? 0 : "auto",
      transform: align === "center" ? "translateX(-50%)" : "none"
    }
  }, text));
}
Object.assign(__ds_scope, { Hint });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Hint.jsx", error: String((e && e.message) || e) }); }

// components/core/MetricTile.jsx
try { (() => {
/** Core-metrics tile (CAGR, Sharpe, …). Value is mono 24/600; a negative
 * P/L-flavored value may use neg (pl-neg) — the ONLY P/L color in the row. */
function MetricTile({
  value,
  label,
  neg = false,
  hint,
  hintAlign = "center",
  style
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: Object.assign({
      borderRadius: 12,
      border: "1px solid var(--line)",
      background: "var(--panel)",
      padding: 16
    }, style)
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: "var(--font-mono)",
      fontSize: 24,
      fontWeight: 600,
      color: neg ? "var(--pl-neg)" : "var(--ink)"
    }
  }, value), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 6,
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      gap: 4
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--font-mono)",
      fontSize: 10.5,
      fontWeight: 500,
      letterSpacing: ".08em",
      color: "var(--ink-4)"
    }
  }, label), hint && /*#__PURE__*/React.createElement(__ds_scope.Hint, {
    text: hint,
    align: hintAlign
  })));
}
Object.assign(__ds_scope, { MetricTile });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/MetricTile.jsx", error: String((e && e.message) || e) }); }

// components/core/Panel.jsx
try { (() => {
/** Shared panel chrome — rounded-14 bordered card with the mono CAPS title
 * row. One definition so surfaces can't drift apart. */
function Panel({
  title,
  right,
  children,
  padding = "16px 20px",
  style
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: Object.assign({
      borderRadius: 14,
      border: "1px solid var(--line)",
      background: "var(--panel)",
      padding: padding
    }, style)
  }, (title || right) && /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 10,
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--font-mono)",
      fontSize: 11.5,
      fontWeight: 500,
      letterSpacing: ".12em",
      color: "var(--ink-4)",
      display: "flex",
      alignItems: "center",
      gap: 8
    }
  }, title), right && /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--font-mono)",
      fontSize: 12,
      color: "var(--ink-4)"
    }
  }, right)), children);
}
Object.assign(__ds_scope, { Panel });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Panel.jsx", error: String((e && e.message) || e) }); }

// components/core/PulsingDots.jsx
try { (() => {
/** The app's "working" glyph: three trust-hue dots pulsing in a wave. */
function PulsingDots({
  size = 5
}) {
  return /*#__PURE__*/React.createElement("span", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 3
    },
    "aria-hidden": "true"
  }, [0, 0.35, 0.7].map(delay => /*#__PURE__*/React.createElement("span", {
    key: delay,
    className: "animate-pin-pulse",
    style: {
      width: size,
      height: size,
      borderRadius: "50%",
      background: "var(--ac)",
      animationDelay: delay + "s"
    }
  })));
}
Object.assign(__ds_scope, { PulsingDots });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/PulsingDots.jsx", error: String((e && e.message) || e) }); }

// components/composer/Composer.jsx
try { (() => {
/** The chat-led strategy composer — the product's primary control surface.
 * Auto-growing textarea (caps at 200px), mic + send circles, shadow-soft. */
function Composer({
  value = "",
  onChange,
  onSubmit,
  placeholder = "sell a 30-delta put on SPY every week, close at 50% profit or 21 days…",
  busy = false,
  showMic = true,
  listening = false,
  onMic
}) {
  const ref = React.useRef(null);
  const [sendHover, setSendHover] = React.useState(false);
  React.useEffect(() => {
    const ta = ref.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
    ta.style.overflowY = ta.scrollHeight > 200 ? "auto" : "hidden";
  }, [value]);
  const ready = value.trim() && !busy;
  return /*#__PURE__*/React.createElement("div", {
    style: {
      borderRadius: 22,
      border: "1px solid var(--line-soft)",
      background: "var(--panel)",
      padding: "10px 12px 10px 24px",
      boxShadow: "var(--shadow-soft)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("textarea", {
    ref: ref,
    rows: 1,
    value: value,
    placeholder: placeholder,
    onChange: e => onChange && onChange(e.target.value),
    onKeyDown: e => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (ready && onSubmit) onSubmit();
      }
    },
    style: {
      width: "100%",
      flex: 1,
      fontSize: 16.5,
      lineHeight: 1.65,
      color: "var(--ink)"
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 6
    }
  }, showMic && /*#__PURE__*/React.createElement("button", {
    onClick: onMic,
    title: listening ? "Stop dictation" : "Dictate your strategy",
    style: {
      display: "flex",
      height: 40,
      width: 40,
      alignItems: "center",
      justifyContent: "center",
      borderRadius: "50%",
      background: listening ? "var(--acd)" : "transparent",
      color: listening ? "var(--ac)" : "var(--ink-4)"
    }
  }, listening ? /*#__PURE__*/React.createElement("span", {
    className: "animate-pin-pulse",
    style: {
      display: "inline-block",
      height: 9,
      width: 9,
      borderRadius: "50%",
      background: "var(--ac)"
    }
  }) : /*#__PURE__*/React.createElement("svg", {
    width: "16",
    height: "16",
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "1.4",
    strokeLinecap: "round"
  }, /*#__PURE__*/React.createElement("rect", {
    x: "5.5",
    y: "1.5",
    width: "5",
    height: "8",
    rx: "2.5"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M3 7.5a5 5 0 0 0 10 0"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "8",
    y1: "12.5",
    x2: "8",
    y2: "14.5"
  }))), /*#__PURE__*/React.createElement("button", {
    onClick: () => ready && onSubmit && onSubmit(),
    disabled: !ready,
    "aria-label": "Compile the strategy",
    title: busy ? "Compiling…" : "Compile ↵",
    onMouseEnter: () => setSendHover(true),
    onMouseLeave: () => setSendHover(false),
    style: {
      display: "flex",
      height: 40,
      width: 40,
      alignItems: "center",
      justifyContent: "center",
      borderRadius: "50%",
      background: ready ? sendHover ? "var(--ink-2)" : "var(--ink)" : "var(--raised-2)",
      color: ready ? "var(--ground)" : "var(--ink-4)",
      cursor: ready ? "pointer" : "not-allowed"
    }
  }, busy ? /*#__PURE__*/React.createElement(__ds_scope.PulsingDots, {
    size: 4
  }) : /*#__PURE__*/React.createElement("svg", {
    width: "17",
    height: "17",
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "1.7",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M2.5 8h11"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M9 3.5L13.5 8 9 12.5"
  }))))));
}
Object.assign(__ds_scope, { Composer });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/composer/Composer.jsx", error: String((e && e.message) || e) }); }

// components/composer/ThinkingIndicator.jsx
try { (() => {
/** Compile-time thinking state: shimmering status line narrating the parse
 * stages, with honest elapsed time. Statuses advance and never loop back. */
const STATUSES = [{
  at: 0,
  text: "Reading your strategy…"
}, {
  at: 3,
  text: "Marking what you stated — entry, exit, sizing…"
}, {
  at: 7,
  text: "Hunting for ambiguity. I don't guess…"
}, {
  at: 12,
  text: "Compiling the spec…"
}, {
  at: 18,
  text: "Validating every field against the schema…"
}, {
  at: 26,
  text: "Double-checking — no field gets a silent default…"
}, {
  at: 38,
  text: "Still working. A slow answer beats a wrong one…"
}];
const DESC = [...STATUSES].reverse();
function ThinkingIndicator() {
  const [elapsed, setElapsed] = React.useState(0);
  React.useEffect(() => {
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 500);
    return () => clearInterval(id);
  }, []);
  const status = DESC.find(s => elapsed >= s.at) || STATUSES[0];
  return /*#__PURE__*/React.createElement("div", {
    className: "animate-fade-rise",
    style: {
      borderRadius: 14,
      border: "1px solid var(--line)",
      background: "var(--panel)",
      padding: "16px 20px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 12
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.PulsingDots, null), /*#__PURE__*/React.createElement("span", {
    key: status.text,
    className: "animate-fade-rise"
  }, /*#__PURE__*/React.createElement("span", {
    className: "thinking-shimmer",
    style: {
      fontSize: 15,
      fontWeight: 500
    }
  }, status.text)), /*#__PURE__*/React.createElement("span", {
    style: {
      marginLeft: "auto",
      fontFamily: "var(--font-mono)",
      fontSize: 12,
      fontVariantNumeric: "tabular-nums",
      color: "var(--ink-4)"
    }
  }, elapsed, "s")), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "8px 0 0",
      paddingLeft: 30,
      fontSize: 12.5,
      lineHeight: 1.55,
      color: "var(--ink-4)"
    }
  }, "If anything is ambiguous, you'll get a question, not a guess."));
}
Object.assign(__ds_scope, { ThinkingIndicator });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/composer/ThinkingIndicator.jsx", error: String((e && e.message) || e) }); }

// components/gauntlet/GauntletProgress.jsx
try { (() => {
/** Run-in-progress: the gauntlet attacking the strategy stage by stage.
 * Stage glyphs ✓ ▶ ○; live feed shows REAL computed numbers only. */
const STAGES = [{
  t: "Backtest",
  n: "fills priced at real bid/ask, never mid"
}, {
  t: "Out-of-sample split",
  n: "last 30% of history kept hidden"
}, {
  t: "Walk-forward windows",
  n: "rolling ~2-month windows"
}, {
  t: "Monte Carlo — 1,000 resamples",
  n: "how much was luck?"
}, {
  t: "Sensitivity sweep",
  n: "does it survive small changes?"
}, {
  t: "The honest verdict",
  n: "grounded in the numbers above"
}];
const HEADINGS = ["Attacking your strategy", "Stress-testing your idea", "Interrogating the edge", "Trying to break it", "Cross-examining the numbers", "Putting the edge on trial"];
function GauntletProgress({
  stage = 0,
  name = "",
  previews = [],
  tip,
  stages = STAGES,
  rotateHeading = true
}) {
  const [hi, setHi] = React.useState(0);
  const [visible, setVisible] = React.useState(true);
  React.useEffect(() => {
    if (!rotateHeading) return;
    const id = setInterval(() => {
      setVisible(false);
      setTimeout(() => {
        setHi(i => (i + 1) % HEADINGS.length);
        setVisible(true);
      }, 350);
    }, 5000);
    return () => clearInterval(id);
  }, [rotateHeading]);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      margin: "0 auto",
      maxWidth: 650
    }
  }, /*#__PURE__*/React.createElement("h2", {
    style: {
      margin: "0 0 6px",
      fontFamily: "var(--font-serif)",
      fontSize: 32,
      fontWeight: 500,
      opacity: visible ? 1 : 0,
      transition: "opacity .3s"
    }
  }, HEADINGS[hi], /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-flex",
      width: "1.4em"
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "animate-pin-pulse"
  }, "."), /*#__PURE__*/React.createElement("span", {
    className: "animate-pin-pulse",
    style: {
      animationDelay: ".35s"
    }
  }, "."), /*#__PURE__*/React.createElement("span", {
    className: "animate-pin-pulse",
    style: {
      animationDelay: ".7s"
    }
  }, "."))), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "0 0 26px",
      fontSize: 15,
      color: "var(--ink-3)"
    }
  }, name), /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 24,
      height: 6,
      overflow: "hidden",
      borderRadius: 3,
      background: "var(--line-softer)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      height: "100%",
      borderRadius: 3,
      background: "var(--ac)",
      transition: "width .5s",
      width: Math.min(100, stage / stages.length * 100) + "%"
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 13
    }
  }, stages.map((st, i) => /*#__PURE__*/React.createElement("div", {
    key: st.t,
    style: {
      display: "flex",
      alignItems: "baseline",
      gap: 12,
      fontFamily: "var(--font-mono)",
      fontSize: 14.5,
      color: i < stage ? "var(--ink-3)" : i === stage ? "var(--ink)" : "var(--ink-5)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-block",
      width: 20
    }
  }, i < stage ? "✓" : i === stage ? "▶" : "○"), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1
    }
  }, st.t), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: "var(--ink-4)"
    }
  }, st.n)))), previews.length > 0 && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 28,
      borderRadius: 14,
      border: "1px solid var(--acb)",
      background: "var(--acd)",
      padding: "16px 20px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 10,
      fontFamily: "var(--font-mono)",
      fontSize: 11,
      fontWeight: 500,
      letterSpacing: ".14em",
      color: "var(--ac)"
    }
  }, "LIVE FROM THE GAUNTLET \u2014 REAL NUMBERS, NOT A LOADING BAR"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 6
    }
  }, previews.map((p, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    style: {
      fontFamily: "var(--font-mono)",
      fontSize: 13.5,
      lineHeight: 1.55,
      color: i === previews.length - 1 ? "var(--ink)" : "var(--ink-3)"
    }
  }, p)))), tip && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 16,
      borderRadius: 14,
      border: "1px solid var(--line)",
      background: "var(--panel)",
      padding: "16px 20px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 6,
      fontFamily: "var(--font-mono)",
      fontSize: 11,
      fontWeight: 500,
      letterSpacing: ".14em",
      color: "var(--ink-4)"
    }
  }, "WHILE YOU WAIT"), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      fontSize: 14.5,
      lineHeight: 1.6,
      color: "var(--ink-2)"
    }
  }, tip)));
}
Object.assign(__ds_scope, { GauntletProgress });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/gauntlet/GauntletProgress.jsx", error: String((e && e.message) || e) }); }

// components/navigation/NavRail.jsx
try { (() => {
/** Left navigation — icon rail (56px) or open sidebar (196px) with the
 * wordmark, four destinations, recent analyses, collapse control.
 * Pass logo/mark srcs relative to the consuming page. */
const ICONS = {
  plus: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 20 20",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "1.8",
    strokeLinecap: "round"
  }, /*#__PURE__*/React.createElement("line", {
    x1: "10",
    y1: "4.5",
    x2: "10",
    y2: "15.5"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "4.5",
    y1: "10",
    x2: "15.5",
    y2: "10"
  })),
  library: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 20 20",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "1.6"
  }, /*#__PURE__*/React.createElement("rect", {
    x: "3.5",
    y: "3.5",
    width: "13",
    height: "5",
    rx: "1.5"
  }), /*#__PURE__*/React.createElement("rect", {
    x: "3.5",
    y: "11.5",
    width: "13",
    height: "5",
    rx: "1.5"
  })),
  data: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 20 20",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "1.8",
    strokeLinecap: "round"
  }, /*#__PURE__*/React.createElement("line", {
    x1: "5",
    y1: "16",
    x2: "5",
    y2: "9"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "10",
    y1: "16",
    x2: "10",
    y2: "4.5"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "15",
    y1: "16",
    x2: "15",
    y2: "12"
  })),
  settings: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 20 20",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "1.6"
  }, /*#__PURE__*/React.createElement("line", {
    x1: "3.5",
    y1: "6.5",
    x2: "16.5",
    y2: "6.5",
    strokeLinecap: "round"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "6.5",
    r: "2.1",
    fill: "var(--navbg)"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "3.5",
    y1: "13.5",
    x2: "16.5",
    y2: "13.5",
    strokeLinecap: "round"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "7.5",
    cy: "13.5",
    r: "2.1",
    fill: "var(--navbg)"
  }))
};
const DEFAULT_ITEMS = [{
  id: "new",
  title: "New Analysis",
  icon: "plus"
}, {
  id: "library",
  title: "Library",
  icon: "library"
}, {
  id: "data",
  title: "Data Observatory",
  icon: "data"
}, {
  id: "settings",
  title: "Settings",
  icon: "settings"
}];
function NavItem({
  item,
  active,
  open,
  onClick
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("button", {
    title: item.title,
    onClick: onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      display: "flex",
      height: 38,
      alignItems: "center",
      borderRadius: 10,
      width: open ? "100%" : 38,
      gap: open ? 12 : 0,
      padding: open ? "0 10px" : 0,
      justifyContent: open ? "flex-start" : "center",
      background: active ? "var(--acd)" : "transparent",
      color: active ? "var(--ac)" : hover ? "var(--ink)" : "var(--ink-3)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      flex: "none",
      display: "flex"
    }
  }, ICONS[item.icon] || ICONS.plus), open && /*#__PURE__*/React.createElement("span", {
    style: {
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
      fontSize: 13,
      fontWeight: 600
    }
  }, item.title));
}
function RecentRow({
  r,
  active,
  onClick
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("button", {
    title: r.name,
    onClick: onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      display: "flex",
      alignItems: "center",
      gap: 6,
      borderRadius: 8,
      padding: "7px 10px",
      fontSize: 12.5,
      textAlign: "left",
      width: "100%",
      background: active ? "var(--raised-2)" : hover ? "var(--raised)" : "transparent",
      color: active ? "var(--ink)" : hover ? "var(--ink-2)" : "var(--ink-4)"
    }
  }, r.running && /*#__PURE__*/React.createElement("span", {
    className: "animate-pin-pulse",
    style: {
      display: "inline-block",
      height: 6,
      width: 6,
      flexShrink: 0,
      borderRadius: "50%",
      background: "var(--ac)"
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap"
    }
  }, r.name));
}
function NavRail({
  open = true,
  active = "new",
  items = DEFAULT_ITEMS,
  recent = [],
  activeRecent = null,
  wordmarkSrc,
  markSrc,
  onNavigate,
  onToggle,
  height = "100%"
}) {
  return /*#__PURE__*/React.createElement("nav", {
    style: {
      position: "relative",
      display: "flex",
      flex: "none",
      flexDirection: "column",
      gap: 8,
      borderRight: "1px solid var(--line-softer)",
      background: "var(--navbg)",
      padding: open ? "14px 10px" : "14px 0",
      width: open ? 196 : 56,
      alignItems: open ? "stretch" : "center",
      height: height,
      boxSizing: "border-box"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 16,
      display: "flex",
      height: 40,
      alignItems: "center",
      padding: open ? "0 6px" : 0
    }
  }, open ? wordmarkSrc ? /*#__PURE__*/React.createElement("img", {
    src: wordmarkSrc,
    alt: "Skeptic",
    style: {
      height: 28,
      width: "auto"
    },
    draggable: false
  }) : /*#__PURE__*/React.createElement("span", {
    style: {
      fontWeight: 650,
      fontSize: 18
    }
  }, "Skeptic") : markSrc ? /*#__PURE__*/React.createElement("img", {
    src: markSrc,
    alt: "Skeptic",
    style: {
      height: 32,
      width: "auto"
    },
    draggable: false
  }) : /*#__PURE__*/React.createElement("span", {
    style: {
      fontWeight: 650,
      fontSize: 18
    }
  }, "S")), items.map(item => /*#__PURE__*/React.createElement(NavItem, {
    key: item.id,
    item: item,
    active: active === item.id,
    open: open,
    onClick: () => onNavigate && onNavigate(item.id)
  })), open && recent.length > 0 && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 20,
      display: "flex",
      minHeight: 0,
      flexDirection: "column",
      overflow: "hidden"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 6,
      padding: "0 10px",
      fontFamily: "var(--font-mono)",
      fontSize: 10,
      fontWeight: 500,
      letterSpacing: ".14em",
      color: "var(--ink-5)"
    }
  }, "RECENT ANALYSES"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 2,
      overflowY: "auto"
    }
  }, recent.map(r => /*#__PURE__*/React.createElement(RecentRow, {
    key: r.id,
    r: r,
    active: activeRecent === r.id,
    onClick: () => onNavigate && onNavigate("run:" + r.id)
  })))), /*#__PURE__*/React.createElement("button", {
    onClick: onToggle,
    title: open ? "Collapse sidebar" : "Expand sidebar",
    style: {
      marginTop: "auto",
      display: "flex",
      height: 38,
      alignItems: "center",
      borderRadius: 10,
      color: "var(--ink-4)",
      width: open ? "100%" : 38,
      gap: open ? 12 : 0,
      padding: open ? "0 10px" : 0,
      justifyContent: open ? "flex-start" : "center"
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: "18",
    height: "18",
    viewBox: "0 0 20 20",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "1.7",
    strokeLinecap: "round",
    strokeLinejoin: "round",
    style: {
      flex: "none",
      transform: open ? "rotate(180deg)" : "none",
      transition: "transform .15s"
    }
  }, /*#__PURE__*/React.createElement("path", {
    d: "M8 6l4 4-4 4"
  })), open && /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      fontWeight: 600
    }
  }, "Collapse")));
}
Object.assign(__ds_scope, { NavRail });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/NavRail.jsx", error: String((e && e.message) || e) }); }

// components/verdict/TrustBand.jsx
try { (() => {
/** The trust scale — a confidence band + marker, never a score or grade.
 * variant="hero" (inside VerdictBlock) or "card" (library rows).
 * withheld renders the dashed full-width refusal band (card only).
 * Trust hue only — P/L tokens are forbidden in this file. */
const LABELS = ["noise", "weak", "suggestive", "robust", "proven"];
function TrustBand({
  variant = "hero",
  band,
  marker,
  withheld = false
}) {
  if (variant === "card") {
    return /*#__PURE__*/React.createElement("div", {
      style: {
        position: "relative",
        height: 14,
        marginBottom: 10
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        position: "absolute",
        left: 0,
        right: 0,
        top: 6,
        height: 2,
        background: "var(--band-track)"
      }
    }), withheld ? /*#__PURE__*/React.createElement("div", {
      style: {
        position: "absolute",
        left: "4%",
        top: 2,
        height: 10,
        width: "92%",
        borderRadius: 4,
        border: "1px dashed var(--line-hover)"
      }
    }) : /*#__PURE__*/React.createElement(React.Fragment, null, band && /*#__PURE__*/React.createElement("div", {
      style: {
        position: "absolute",
        top: 2,
        height: 10,
        borderRadius: 4,
        border: "1px solid var(--acb)",
        background: "var(--acd)",
        left: "min(" + band.left + ", calc(100% - " + band.width + "))",
        width: band.width
      }
    }), marker && /*#__PURE__*/React.createElement("div", {
      style: {
        position: "absolute",
        top: 0,
        height: 14,
        width: 3,
        borderRadius: 2,
        background: "var(--ac)",
        left: "min(" + marker + ", calc(100% - 4px))"
      }
    })));
  }
  return /*#__PURE__*/React.createElement("div", {
    style: {
      position: "relative",
      margin: "16px 4px 2px",
      height: 46
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      position: "absolute",
      left: 0,
      right: 0,
      top: 15,
      height: 2,
      background: "var(--band-track)"
    }
  }), band && /*#__PURE__*/React.createElement("div", {
    style: {
      position: "absolute",
      top: 7,
      height: 18,
      borderRadius: 5,
      border: "1px solid var(--acb)",
      background: "var(--acd)",
      left: "min(" + band.left + ", calc(100% - " + band.width + "))",
      width: band.width
    }
  }), marker && /*#__PURE__*/React.createElement("div", {
    style: {
      position: "absolute",
      top: 3,
      height: 26,
      width: 4,
      borderRadius: 2,
      background: "var(--ac)",
      left: "min(" + marker + ", calc(100% - 4px))"
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      position: "absolute",
      bottom: 0,
      left: 0,
      right: 0,
      display: "flex",
      justifyContent: "space-between",
      fontFamily: "var(--font-mono)",
      fontSize: 10,
      color: "var(--ink-4)"
    }
  }, LABELS.map(l => /*#__PURE__*/React.createElement("span", {
    key: l
  }, l))));
}
Object.assign(__ds_scope, { TrustBand });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/verdict/TrustBand.jsx", error: String((e && e.message) || e) }); }

// components/verdict/VerdictBlock.jsx
try { (() => {
/** The Verdict Block — the signature element. Headline first (the
 * uncomfortable part), trust band, attack chips, evidence vs where-it-breaks,
 * caveats. The refusal state is a first-class design, not an error.
 * COLOR CONTRACT: trust hue family only; P/L tokens never appear here. */
const LABEL = {
  fontFamily: "var(--font-mono)",
  fontSize: 11.5,
  fontWeight: 500,
  letterSpacing: ".14em",
  color: "var(--ac)"
};
function VerdictBlock({
  verdict,
  narrationPending = false,
  regraded = null,
  dataHref = "#"
}) {
  const v = verdict || {};
  return /*#__PURE__*/React.createElement("div", {
    style: {
      borderRadius: 16,
      padding: "28px",
      border: (v.refusal ? "1px dashed" : "1px solid") + " var(--acb)",
      background: "linear-gradient(180deg, var(--acd), var(--ac-faint))"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 10,
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: LABEL
  }, "VERDICT \u2014 THE HONEST READ"), /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--font-mono)",
      fontSize: 12,
      fontWeight: 500,
      color: "var(--ac)"
    }
  }, v.survived)), /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 860,
      fontFamily: "var(--font-serif)",
      fontSize: 32,
      fontWeight: 500,
      lineHeight: 1.25
    }
  }, v.headline), narrationPending && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 8,
      display: "flex",
      alignItems: "center",
      gap: 8
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.PulsingDots, {
    size: 4
  }), /*#__PURE__*/React.createElement("span", {
    className: "thinking-shimmer",
    style: {
      fontSize: 13
    }
  }, "still writing the narration \u2014 every number here is already final")), regraded && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 10,
      display: "inline-flex",
      borderRadius: 999,
      border: "1px dashed var(--acb)",
      padding: "4px 12px",
      fontFamily: "var(--font-mono)",
      fontSize: 11.5,
      color: "var(--ac)"
    }
  }, "re-judged at your evidence bar: ", regraded.bar, " trades (this run was scored at ", regraded.ranAt, ")"), !v.refusal && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(__ds_scope.TrustBand, {
    variant: "hero",
    band: v.band,
    marker: v.marker
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 10,
      display: "flex",
      flexWrap: "wrap",
      gap: 6
    }
  }, (v.chips || []).map(txt => /*#__PURE__*/React.createElement("span", {
    key: txt,
    style: {
      borderRadius: 999,
      border: "1px solid var(--acb)",
      padding: "4px 12px",
      fontFamily: "var(--font-mono)",
      fontSize: 12,
      fontWeight: 500,
      color: "var(--ac)"
    }
  }, txt))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 16,
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: 20
    }
  }, [["HOLDS UP", v.evidence || []], ["WHERE IT BREAKS", v.breaks || []]].map(([title, items]) => /*#__PURE__*/React.createElement("div", {
    key: title
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 8,
      fontFamily: "var(--font-mono)",
      fontSize: 11.5,
      fontWeight: 500,
      letterSpacing: ".12em",
      color: "var(--ac)"
    }
  }, title), items.map(t => /*#__PURE__*/React.createElement("div", {
    key: t,
    style: {
      display: "flex",
      gap: 8,
      fontSize: 14.5,
      lineHeight: 1.6,
      color: "var(--ink-2)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--ink-4)"
    }
  }, "\xB7"), /*#__PURE__*/React.createElement("span", null, t)))))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 16,
      fontSize: 13.5,
      lineHeight: 1.6,
      color: "var(--ink-3)"
    }
  }, v.caveat)), v.refusal && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "16px 0 0",
      maxWidth: 760,
      fontSize: 15.5,
      lineHeight: 1.65,
      color: "var(--ink-2)"
    }
  }, v.refusalBody), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 14,
      display: "flex",
      flexWrap: "wrap",
      alignItems: "center",
      gap: 10,
      borderRadius: 10,
      border: "1px dashed var(--acb)",
      padding: "10px 14px"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--font-mono)",
      fontSize: 13,
      color: "var(--ac)"
    }
  }, v.refusalUnlock), /*#__PURE__*/React.createElement("a", {
    href: dataHref,
    style: {
      marginLeft: "auto",
      fontFamily: "var(--font-mono)",
      fontSize: 12,
      color: "var(--ink-3)"
    }
  }, "track progress in Data \u2192"))));
}
Object.assign(__ds_scope, { VerdictBlock });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/verdict/VerdictBlock.jsx", error: String((e && e.message) || e) }); }

// ui_kits/app/App.jsx
try { (() => {
/* App shell — NavRail + screen router; theme/accent applied live. */
const appDS = window.SkepticDesignSystem_68b393;
function resolveTheme(theme) {
  if (theme === "light" || theme === "dark") return theme;
  const h = Number(new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour12: false,
    hour: "2-digit"
  }).format(new Date())) % 24;
  return h >= 8 && h < 18 ? "light" : "dark";
}
function App() {
  if (!window.SkepticDesignSystem_68b393) {
    return /*#__PURE__*/React.createElement("div", {
      style: {
        margin: "20vh auto 0",
        maxWidth: 460,
        borderRadius: 14,
        border: "1px dashed var(--line-hover)",
        padding: "28px 30px",
        textAlign: "center",
        fontFamily: "var(--font-mono)",
        fontSize: 12.5,
        lineHeight: 1.6,
        color: "var(--ink-3)"
      }
    }, "design-system bundle not compiled yet \u2014 reload this page in a moment");
  }
  const [nav, setNav] = React.useState("new");
  const [runId, setRunId] = React.useState(null);
  const [resetKey, setResetKey] = React.useState(0);
  const [settings, setSettings] = React.useState(() => {
    try {
      return Object.assign({
        theme: "dark",
        accent: "cyan"
      }, JSON.parse(localStorage.getItem("skeptic-kit-settings") || "{}"));
    } catch (e) {
      return {
        theme: "dark",
        accent: "cyan"
      };
    }
  });
  const resolved = resolveTheme(settings.theme);
  React.useEffect(() => {
    document.documentElement.dataset.theme = resolved;
    document.documentElement.dataset.accent = settings.accent;
    try {
      localStorage.setItem("skeptic-kit-settings", JSON.stringify(settings));
    } catch (e) {}
  }, [settings, resolved]);
  const updateSettings = patch => setSettings(s => Object.assign({}, s, patch));
  const suffix = resolved === "light" ? "black" : "white";
  const recent = window.SkepticDemo.library.slice(0, 5).map(r => ({
    id: r.id,
    name: r.name,
    running: r.running
  }));
  const navigate = id => {
    if (id.indexOf("run:") === 0) {
      const rid = id.slice(4);
      if (window.SkepticDemo.runsById[rid]) {
        setRunId(rid);
        setNav("run");
      }
      return;
    }
    if (id === "new") setResetKey(k => k + 1);
    setNav(id);
  };
  const openRun = rid => {
    if (window.SkepticDemo.runsById[rid]) {
      setRunId(rid);
      setNav("run");
    }
  };
  let main = null;
  if (nav === "new") main = /*#__PURE__*/React.createElement(HomeScreen, {
    key: resetKey
  });else if (nav === "library") main = /*#__PURE__*/React.createElement(LibraryScreen, {
    onOpen: openRun,
    onNew: () => navigate("new")
  });else if (nav === "run") main = /*#__PURE__*/React.createElement(ResultsScreen, {
    run: window.SkepticDemo.runsById[runId] || window.SkepticDemo.runFresh,
    onBack: () => setNav("library")
  });else if (nav === "settings") main = /*#__PURE__*/React.createElement(SettingsScreen, {
    settings: Object.assign({}, settings, {
      resolved: resolved
    }),
    onSettings: updateSettings
  });else if (nav === "data") main = /*#__PURE__*/React.createElement("div", {
    style: {
      margin: "18vh auto 0",
      maxWidth: 560,
      borderRadius: 14,
      border: "1px dashed var(--line-hover)",
      padding: "40px 32px",
      textAlign: "center"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: "var(--font-mono)",
      fontSize: 12.5,
      color: "var(--ink-3)"
    }
  }, "Data Observatory \u2014 not recreated in this kit"), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 8,
      fontSize: 12.5,
      lineHeight: 1.6,
      color: "var(--ink-4)"
    }
  }, "The honesty telemetry screen (coverage lanes, collection streak, named blind spots) lives at frontend/app/data/page.tsx in the repo."));
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      height: "100vh",
      overflow: "hidden"
    }
  }, /*#__PURE__*/React.createElement(appDS.NavRail, {
    open: true,
    active: nav === "run" ? "library" : nav,
    recent: recent,
    activeRecent: nav === "run" ? runId : null,
    wordmarkSrc: "../../assets/brand/wordmark-" + suffix + ".svg",
    markSrc: "../../assets/brand/s-mark-" + suffix + ".svg",
    onNavigate: navigate,
    height: "100%"
  }), /*#__PURE__*/React.createElement("main", {
    style: {
      flex: 1,
      overflow: "auto"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      margin: "0 auto",
      maxWidth: 1620,
      padding: "32px 34px 40px"
    }
  }, main)));
}
ReactDOM.createRoot(document.getElementById("root")).render(/*#__PURE__*/React.createElement(App, null));
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/app/App.jsx", error: String((e && e.message) || e) }); }

// ui_kits/app/HomeScreen.jsx
try { (() => {
/* New Analysis — the whole run flow as one chat-led surface:
   compose → thinking → clarify → gauntlet → results. */
const homeDS = window.SkepticDesignSystem_68b393;
const HOME_PRESETS = [{
  label: "Weekly income put",
  structure: "short put",
  phrase: "sell a 30-delta put on SPY every week, close at 50% profit or 21 days"
}, {
  label: "Conservative income put",
  structure: "short put",
  phrase: "sell a 16-delta put on SPY monthly, 45 DTE, close at 50% profit or 21 DTE"
}, {
  label: "Defined-risk put spread",
  structure: "put credit spread",
  phrase: "sell a 25-delta put spread on SPY, $5 wide, 45 DTE, close at 50% profit"
}, {
  label: "Fade-the-rally call spread",
  structure: "call credit spread",
  phrase: "sell a 25-delta call spread on SPY, $5 wide, 30 DTE, close at 50% profit, stop at 2x credit"
}, {
  label: "Calm-market condor",
  structure: "iron condor",
  phrase: "iron condor on SPY at 16 delta, 45 DTE, exit at 21 DTE or 2x credit stop"
}, {
  label: "Covered-call income",
  structure: "covered call",
  phrase: "covered call on SPY, sell the 30-delta monthly, roll at 21 DTE"
}, {
  label: "Dip-buyer call",
  structure: "long call",
  phrase: "buy a 60-day SPY call after a 5% pullback, sell at +100% or stop 50%"
}, {
  label: "Crash-insurance put",
  structure: "long put",
  phrase: "buy a 10-delta SPY put, 45 DTE, sell at +200% or hold to expiry"
}];
const GAUNTLET_PREVIEWS = ["backtest: 214 trades · 68% win rate · profit factor 1.62", "out-of-sample: Sharpe 0.93 — keeps 71% of in-sample", "walk-forward: 7 of 9 windows positive", "Monte Carlo: 5th percentile ends at $27,100 — above water", "sensitivity: plateau — ±20% nudges keep 80%+ of the result"];
const WAIT_TIPS = ["Fills never happen at mid price — buys lean toward the ask, sells toward the bid, plus slippage.", "The trust band is a range, not a score — precision would be dishonest.", "The library sorts by trust, not by return. On purpose.", "A 'plateau' in the nudge test is good: small settings changes don't wreck the result.", "Below your minimum-trades bar the verdict is withheld — good-looking numbers don't override it."];
const HOME_HEADLINES = ["Describe a strategy. I'll try to break it.", "Bring your thesis. I'll bring the evidence.", "Pitch me a trade. I'll play the skeptic.", "Your idea versus six years of market data. Go."];
function PromptBubble({
  text
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 16,
      display: "flex",
      justifyContent: "flex-end"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: "75%",
      borderRadius: "12px 12px 4px 12px",
      border: "1px solid var(--line)",
      background: "var(--raised)",
      padding: "10px 14px",
      fontFamily: "var(--font-mono)",
      fontSize: 13,
      lineHeight: 1.55,
      color: "var(--ink-2)"
    }
  }, "\u201C", text, "\u201D"));
}
function HomeScreen({
  onDone
}) {
  const [phase, setPhase] = React.useState("compose");
  const [mode, setMode] = React.useState("text");
  const [text, setText] = React.useState("");
  const [stage, setStage] = React.useState(0);
  const [tipIdx, setTipIdx] = React.useState(0);
  const headline = React.useMemo(() => HOME_HEADLINES[Math.floor(Date.now() / 60000) % HOME_HEADLINES.length], []);
  React.useEffect(() => {
    if (phase === "thinking") {
      const id = setTimeout(() => setPhase("clarify"), 4200);
      return () => clearTimeout(id);
    }
    if (phase === "running") {
      setStage(0);
      const id = setInterval(() => setStage(s => {
        if (s + 1 > 6) {
          clearInterval(id);
          setPhase("results");
          return s;
        }
        return s + 1;
      }), 1500);
      const tid = setInterval(() => setTipIdx(i => (i + 1) % WAIT_TIPS.length), 6000);
      return () => {
        clearInterval(id);
        clearInterval(tid);
      };
    }
  }, [phase]);
  if (phase === "results") {
    return /*#__PURE__*/React.createElement(ResultsScreen, {
      run: window.SkepticDemo.runFresh,
      onEditSpec: () => setPhase("compose"),
      onNew: () => {
        setText("");
        setPhase("compose");
      }
    });
  }
  if (phase === "running") {
    return /*#__PURE__*/React.createElement("div", {
      style: {
        paddingTop: "7vh"
      }
    }, /*#__PURE__*/React.createElement(homeDS.GauntletProgress, {
      stage: Math.min(stage, 5),
      name: text,
      previews: GAUNTLET_PREVIEWS.slice(0, Math.max(0, Math.min(stage, 5))),
      tip: WAIT_TIPS[tipIdx]
    }));
  }
  if (phase === "thinking" || phase === "clarify") {
    return /*#__PURE__*/React.createElement("div", {
      style: {
        margin: "0 auto",
        maxWidth: 684
      }
    }, /*#__PURE__*/React.createElement("button", {
      onClick: () => setPhase("compose"),
      style: {
        marginBottom: 18,
        fontSize: 12.5,
        color: "var(--ink-4)"
      }
    }, "\u2039 edit input"), /*#__PURE__*/React.createElement(PromptBubble, {
      text: text
    }), phase === "thinking" ? /*#__PURE__*/React.createElement(homeDS.ThinkingIndicator, null) : /*#__PURE__*/React.createElement(homeDS.QuestionCard, {
      index: 1,
      total: 1,
      question: "Two exits could apply at 21 days \u2014 take whichever hits first, or profit target only?",
      options: ["whichever hits first", "profit target only", "time exit only"],
      onAnswer: () => setPhase("running")
    }));
  }
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      margin: "9vh auto 36px",
      maxWidth: 900
    }
  }, /*#__PURE__*/React.createElement("h1", {
    style: {
      margin: 0,
      textAlign: "center",
      fontFamily: "var(--font-serif)",
      fontSize: "clamp(32px, 3.6vw, 44px)",
      fontWeight: 500,
      lineHeight: 1.12,
      letterSpacing: "-.01em"
    }
  }, headline)), /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 16,
      display: "flex",
      justifyContent: "center",
      gap: 4
    }
  }, [["text", "Describe It"], ["chart", "Show on Chart"]].map(([m, label]) => /*#__PURE__*/React.createElement("button", {
    key: m,
    onClick: () => setMode(m),
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8,
      borderRadius: 999,
      padding: "6px 14px",
      fontSize: 13.5,
      fontWeight: 500,
      background: mode === m ? "var(--raised-2)" : "transparent",
      color: mode === m ? "var(--ink)" : "var(--ink-4)"
    }
  }, m === "text" ? /*#__PURE__*/React.createElement("svg", {
    width: "14",
    height: "14",
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "1.4",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M11.1 1.9l3 3L6 13l-3.6.6L3 10z"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M9.6 3.4l3 3"
  })) : /*#__PURE__*/React.createElement("svg", {
    width: "14",
    height: "14",
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "1.3",
    strokeLinecap: "round"
  }, /*#__PURE__*/React.createElement("line", {
    x1: "3.2",
    y1: "5.2",
    x2: "3.2",
    y2: "13.2"
  }), /*#__PURE__*/React.createElement("rect", {
    x: "2",
    y: "7",
    width: "2.4",
    height: "3.6",
    rx: "0.5",
    fill: "currentColor",
    stroke: "none"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "8",
    y1: "1.8",
    x2: "8",
    y2: "10.4"
  }), /*#__PURE__*/React.createElement("rect", {
    x: "6.8",
    y: "3.6",
    width: "2.4",
    height: "4.2",
    rx: "0.5",
    fill: "currentColor",
    stroke: "none"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "12.8",
    y1: "4.4",
    x2: "12.8",
    y2: "14.2"
  }), /*#__PURE__*/React.createElement("rect", {
    x: "11.6",
    y: "6.6",
    width: "2.4",
    height: "3.8",
    rx: "0.5",
    fill: "currentColor",
    stroke: "none"
  })), label))), mode === "text" ? /*#__PURE__*/React.createElement("div", {
    style: {
      margin: "0 auto",
      maxWidth: 960
    }
  }, /*#__PURE__*/React.createElement(homeDS.Composer, {
    value: text,
    onChange: setText,
    onSubmit: () => text.trim() && setPhase("thinking")
  }), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "14px 0 0",
      textAlign: "center",
      fontSize: 12.5,
      color: "var(--ink-4)"
    }
  }, "Research tool, not financial advice. Backtests overstate live results."), /*#__PURE__*/React.createElement("div", {
    style: {
      margin: "18px auto 0",
      display: "flex",
      flexWrap: "wrap",
      justifyContent: "center",
      gap: 8
    }
  }, /*#__PURE__*/React.createElement(homeDS.CoverageChip, {
    label: "SPY chains",
    fill: 0.92,
    range: "Jan \u201920 \u2192 now"
  }), /*#__PURE__*/React.createElement(homeDS.CoverageChip, {
    label: "QQQ/IWM chains",
    fill: 0.06,
    range: "Jul \u201926 \u2192 now"
  }), /*#__PURE__*/React.createElement(homeDS.CoverageChip, {
    label: "minute bars",
    fill: 0.36,
    range: "Feb \u201924 \u2192 now"
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      alignSelf: "center",
      fontFamily: "var(--font-mono)",
      fontSize: 11,
      color: "var(--ink-4)"
    }
  }, "day 379 of collection \u2192")), /*#__PURE__*/React.createElement("div", {
    style: {
      margin: "28px auto 0",
      display: "flex",
      maxWidth: 1300,
      flexWrap: "wrap",
      justifyContent: "center",
      gap: 10
    }
  }, HOME_PRESETS.map(p => /*#__PURE__*/React.createElement(homeDS.PresetChip, {
    key: p.label,
    label: p.label,
    structure: p.structure,
    phrase: p.phrase,
    onClick: () => setText(p.phrase)
  })))) : /*#__PURE__*/React.createElement("div", {
    className: "animate-chart-reveal",
    style: {
      margin: "0 auto",
      maxWidth: 960,
      borderRadius: 14,
      border: "1px dashed var(--line-hover)",
      padding: "48px 32px",
      textAlign: "center"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: "var(--font-mono)",
      fontSize: 12.5,
      color: "var(--ink-3)"
    }
  }, "chart mode not recreated in this kit"), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 6,
      fontSize: 12.5,
      color: "var(--ink-4)"
    }
  }, "see frontend/components/composer/chart-teach.tsx and charts/market-chart.tsx in the repo")));
}
Object.assign(window, {
  HomeScreen
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/app/HomeScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/app/LibraryScreen.jsx
try { (() => {
/* Strategy Library — sorted by trust, not by return. */
const libDS = window.SkepticDesignSystem_68b393;
function LibraryCard({
  r,
  onOpen
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("button", {
    onClick: () => r.id && onOpen(r.id),
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      borderRadius: 14,
      border: "1px solid " + (hover ? "var(--line-hover)" : "var(--line)"),
      background: "var(--panel)",
      padding: 20,
      textAlign: "left",
      display: "block",
      width: "100%"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8,
      fontFamily: "var(--font-mono)",
      fontSize: 15,
      fontWeight: 500,
      color: "var(--ink)"
    }
  }, r.running && /*#__PURE__*/React.createElement("span", {
    className: "animate-pin-pulse",
    style: {
      display: "inline-block",
      height: 8,
      width: 8,
      flexShrink: 0,
      borderRadius: "50%",
      background: "var(--ac)"
    }
  }), r.name), /*#__PURE__*/React.createElement("div", {
    style: {
      margin: "4px 0 14px",
      fontFamily: "var(--font-mono)",
      fontSize: 12,
      color: "var(--ink-4)"
    }
  }, r.meta), r.running ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      minHeight: 72,
      flexDirection: "column",
      justifyContent: "center",
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: "var(--font-mono)",
      fontSize: 12,
      letterSpacing: ".1em",
      color: "var(--ac)"
    }
  }, "GAUNTLET IN PROGRESS \u2014 STAGE ", (r.stage || 0) + 1, " OF 6"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      color: "var(--ink-4)"
    }
  }, "Open to watch it live.")) : /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(libDS.TrustBand, {
    variant: "card",
    band: r.band,
    marker: r.marker,
    withheld: r.withheld
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13.5,
      fontStyle: "italic",
      lineHeight: 1.55,
      color: "var(--ink-2)"
    }
  }, r.quote)));
}
function LibraryScreen({
  onOpen,
  onNew
}) {
  const runs = window.SkepticDemo.library;
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h1", {
    style: {
      margin: "0 0 4px",
      fontFamily: "var(--font-serif)",
      fontSize: 32,
      fontWeight: 500
    }
  }, "Library"), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "0 0 22px",
      fontSize: 15,
      color: "var(--ink-3)"
    }
  }, "Sorted by trust, not by return."), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: 14
    }
  }, runs.map(r => /*#__PURE__*/React.createElement(LibraryCard, {
    key: r.id,
    r: r,
    onOpen: onOpen
  })), /*#__PURE__*/React.createElement("button", {
    onClick: onNew,
    style: {
      display: "flex",
      minHeight: 140,
      alignItems: "center",
      justifyContent: "center",
      borderRadius: 14,
      border: "1px dashed var(--line-hover)",
      padding: 20,
      fontSize: 14.5,
      color: "var(--ink-4)"
    }
  }, "+ New Analysis")), /*#__PURE__*/React.createElement(libDS.Disclaimer, {
    short: true
  }));
}
Object.assign(window, {
  LibraryScreen
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/app/LibraryScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/app/ResultsScreen.jsx
try { (() => {
/* Results / Verdict — verdict-first; the equity curve lives below the fold
   of attention. Composes DS primitives. */
const DS = window.SkepticDesignSystem_68b393;
function EquityChart({
  run
}) {
  const [hover, setHover] = React.useState(null);
  const wrapRef = React.useRef(null);
  const series = run.equity,
    dd = run.drawdown;
  const vs = series.map(p => p.v);
  const lo = Math.min(...vs),
    hi = Math.max(...vs),
    span = hi - lo || 1;
  const xFor = i => i / Math.max(series.length - 1, 1) * 860;
  const yFor = v => 14 + (1 - (v - lo) / span) * (200 - 28);
  const onMove = e => {
    const rect = wrapRef.current.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    setHover(Math.round(frac * (series.length - 1)));
  };
  const h = hover !== null && series[hover] ? hover : null;
  const hoverFrac = h !== null ? h / Math.max(series.length - 1, 1) : 0;
  const fmt$ = v => "$" + Math.round(v).toLocaleString();
  return /*#__PURE__*/React.createElement(DS.Panel, {
    style: {
      marginTop: 14
    },
    title: /*#__PURE__*/React.createElement("span", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 8
      }
    }, run.oosShadeX < 860 ? "EQUITY — OUT-OF-SAMPLE SHADED" : "EQUITY", " ", /*#__PURE__*/React.createElement(DS.Hint, {
      text: "Account value over time, after commissions and slippage. The shaded strip is out-of-sample history; the red line below is drawdown."
    })),
    right: run.startLabel
  }, /*#__PURE__*/React.createElement("div", {
    ref: wrapRef,
    style: {
      position: "relative",
      cursor: "crosshair"
    },
    onMouseMove: onMove,
    onMouseLeave: () => setHover(null)
  }, /*#__PURE__*/React.createElement("svg", {
    width: "100%",
    viewBox: "0 0 860 200",
    style: {
      display: "block"
    }
  }, run.oosShadeX < 860 && /*#__PURE__*/React.createElement("g", null, /*#__PURE__*/React.createElement("rect", {
    x: run.oosShadeX,
    y: "0",
    width: 860 - run.oosShadeX,
    height: "200",
    fill: "var(--oos-shade)"
  }), /*#__PURE__*/React.createElement("text", {
    x: run.oosShadeX + 8,
    y: "14",
    fill: "var(--ink-5)",
    fontSize: "10",
    fontFamily: "var(--font-plex-mono)"
  }, "OUT-OF-SAMPLE \u2192")), /*#__PURE__*/React.createElement("line", {
    x1: "0",
    y1: "50",
    x2: "860",
    y2: "50",
    stroke: "var(--grid)"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "0",
    y1: "100",
    x2: "860",
    y2: "100",
    stroke: "var(--grid)"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "0",
    y1: "150",
    x2: "860",
    y2: "150",
    stroke: "var(--grid)"
  }), /*#__PURE__*/React.createElement("text", {
    x: "4",
    y: "12",
    fill: "var(--ink-5)",
    fontSize: "10",
    fontFamily: "var(--font-plex-mono)"
  }, fmt$(hi)), /*#__PURE__*/React.createElement("text", {
    x: "4",
    y: "196",
    fill: "var(--ink-5)",
    fontSize: "10",
    fontFamily: "var(--font-plex-mono)"
  }, fmt$(lo)), /*#__PURE__*/React.createElement("polyline", {
    points: run.equityPoints,
    fill: "none",
    stroke: "var(--chart-bright)",
    strokeWidth: "1.8"
  }), h !== null && /*#__PURE__*/React.createElement("g", null, /*#__PURE__*/React.createElement("line", {
    x1: xFor(h),
    y1: "0",
    x2: xFor(h),
    y2: "200",
    stroke: "var(--crosshair)",
    strokeWidth: "1"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: xFor(h),
    cy: yFor(series[h].v),
    r: "3.5",
    fill: "var(--chart-bright)"
  }))), /*#__PURE__*/React.createElement("svg", {
    width: "100%",
    viewBox: "0 0 860 54",
    style: {
      display: "block",
      marginTop: 6
    }
  }, /*#__PURE__*/React.createElement("line", {
    x1: "0",
    y1: "6",
    x2: "860",
    y2: "6",
    stroke: "var(--grid)"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: run.drawdownPoints,
    fill: "none",
    stroke: "var(--pl-neg)",
    strokeWidth: "1.3"
  }), h !== null && /*#__PURE__*/React.createElement("line", {
    x1: xFor(h),
    y1: "0",
    x2: xFor(h),
    y2: "54",
    stroke: "var(--crosshair)",
    strokeWidth: "1"
  })), h !== null && /*#__PURE__*/React.createElement("div", {
    style: Object.assign({
      pointerEvents: "none",
      position: "absolute",
      top: 4,
      zIndex: 10,
      borderRadius: 9,
      border: "1px solid var(--line)",
      background: "var(--raised)",
      padding: "8px 12px",
      fontFamily: "var(--font-mono)",
      fontSize: 11.5,
      lineHeight: 1.6,
      boxShadow: "var(--shadow-pop)"
    }, hoverFrac > 0.62 ? {
      right: (1 - hoverFrac) * 100 + "%",
      marginRight: 10
    } : {
      left: hoverFrac * 100 + "%",
      marginLeft: 10
    })
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      color: "var(--ink-4)"
    }
  }, series[h].t, run.oosShadeX < 860 && xFor(h) > run.oosShadeX && /*#__PURE__*/React.createElement("span", {
    style: {
      marginLeft: 6,
      color: "var(--ac)"
    }
  }, "OOS")), /*#__PURE__*/React.createElement("div", {
    style: {
      color: "var(--ink)"
    }
  }, fmt$(series[h].v)), /*#__PURE__*/React.createElement("div", {
    style: {
      color: "var(--pl-neg)"
    }
  }, "drawdown \u2212", dd[h].v.toFixed(1), "%"))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 6,
      display: "flex",
      justifyContent: "space-between",
      fontFamily: "var(--font-mono)",
      fontSize: 10.5,
      color: "var(--ink-4)"
    }
  }, /*#__PURE__*/React.createElement("span", null, "drawdown \u2014 P/L red lives only here, never in the verdict"), /*#__PURE__*/React.createElement("span", null, series[0].t, " \xB7 ", series[series.length - 1].t)));
}
function HonestyPanels({
  run
}) {
  const hp = run.honesty;
  const bar = w => /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 8,
      height: 9,
      overflow: "hidden",
      borderRadius: 3,
      background: "var(--line-softer)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      height: "100%",
      borderRadius: 3,
      background: "var(--chart)",
      width: w
    }
  }));
  return /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 14,
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: 12
    }
  }, /*#__PURE__*/React.createElement(DS.Panel, {
    title: /*#__PURE__*/React.createElement("span", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 8
      }
    }, "IN-SAMPLE VS OUT-OF-SAMPLE ", /*#__PURE__*/React.createElement(DS.Hint, {
      text: "The last 30% of history is judged separately. A real edge holds up on data it never saw; a curve-fit one collapses there."
    }))
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 4,
      fontFamily: "var(--font-mono)",
      fontSize: 12.5,
      color: "var(--ink-3)"
    }
  }, "IS sharpe ", hp.isSharpe), bar(hp.bar1), /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 4,
      fontFamily: "var(--font-mono)",
      fontSize: 12.5,
      color: "var(--ink-3)"
    }
  }, "OOS sharpe ", hp.oosSharpe), bar(hp.bar2), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 12,
      fontSize: 14,
      color: "var(--ink-2)"
    }
  }, hp.notes[0])), /*#__PURE__*/React.createElement(DS.Panel, {
    title: /*#__PURE__*/React.createElement("span", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 8
      }
    }, "WALK-FORWARD \u2014 ", hp.wf.length, " WINDOWS ", /*#__PURE__*/React.createElement(DS.Hint, {
      text: "P/L in rolling ~2-month windows. A real edge wins in most windows \u2014 not one lucky stretch."
    }))
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      height: 64,
      alignItems: "flex-end",
      gap: 3
    }
  }, hp.wf.map((w, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    title: w.t,
    style: {
      minWidth: 2,
      flex: 1,
      borderRadius: "2px 2px 0 0",
      cursor: "help",
      background: w.pos ? "var(--pl-pos)" : "var(--pl-neg)",
      height: w.h
    }
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 12,
      fontSize: 14,
      color: "var(--ink-2)"
    }
  }, hp.notes[1])), /*#__PURE__*/React.createElement(DS.Panel, {
    title: /*#__PURE__*/React.createElement("span", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 8
      }
    }, "MONTE CARLO \u2014 1,000 RESAMPLES ", /*#__PURE__*/React.createElement(DS.Hint, {
      text: "The trade order reshuffled 1,000 times to show what luck alone could produce."
    }))
  }, /*#__PURE__*/React.createElement("svg", {
    width: "100%",
    viewBox: "0 0 400 100",
    style: {
      display: "block",
      overflow: "visible"
    }
  }, /*#__PURE__*/React.createElement("polyline", {
    points: run.mc.p95,
    fill: "none",
    stroke: "var(--chart-mid)",
    strokeWidth: "1.2"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: run.mc.p50,
    fill: "none",
    stroke: "var(--chart)",
    strokeWidth: "1.7"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: run.mc.p05,
    fill: "none",
    stroke: "var(--chart-mid)",
    strokeWidth: "1.2"
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 12,
      display: "grid",
      gridTemplateColumns: "1fr 1fr 1fr",
      gap: 8,
      fontFamily: "var(--font-mono)",
      fontSize: 11
    }
  }, [["95th pct", run.mcTerm.p95, "var(--chart-mid)"], ["median", run.mcTerm.p50, "var(--chart)"], ["5th pct", run.mcTerm.p05, "var(--chart-mid)"]].map(([l, v, c]) => /*#__PURE__*/React.createElement("div", {
    key: l,
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 4
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 6,
      color: "var(--ink-4)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-block",
      height: 3,
      width: 14,
      borderRadius: 999,
      background: c
    }
  }), l), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      color: "var(--ink)"
    }
  }, v)))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 10,
      fontSize: 14,
      color: "var(--ink-2)"
    }
  }, hp.notes[2])), /*#__PURE__*/React.createElement(DS.Panel, {
    title: /*#__PURE__*/React.createElement("span", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 8
      }
    }, "SENSITIVITY \u2014 PER-PARAMETER SWEEP ", /*#__PURE__*/React.createElement(DS.Hint, {
      align: "right",
      text: "Each parameter nudged around its specced value and the backtest re-run. Brighter = better Sharpe. A real edge survives nudges (plateau); a fragile one collapses (cliff)."
    }))
  }, run.sensitivity.length ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 4
    }
  }, run.sensitivity.map(row => /*#__PURE__*/React.createElement("div", {
    key: row.name,
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 110,
      flexShrink: 0,
      fontFamily: "var(--font-mono)",
      fontSize: 10.5,
      color: "var(--ink-4)"
    }
  }, row.name), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      flex: 1,
      gap: 4,
      gridTemplateColumns: "repeat(" + row.cells.length + ", minmax(0, 1fr))"
    }
  }, row.cells.map((cell, ci) => /*#__PURE__*/React.createElement("div", {
    key: ci,
    title: row.name + " " + cell.label + " → brighter is better",
    style: {
      display: "flex",
      height: 28,
      cursor: "help",
      alignItems: "center",
      justifyContent: "center",
      borderRadius: 4,
      fontFamily: "var(--font-mono)",
      fontSize: 10,
      background: "rgb(var(--heat-rgb) / " + cell.o + ")",
      color: cell.o > 0.55 ? "var(--on-accent)" : "var(--ink-3)",
      boxShadow: ci === row.base ? "0 0 0 1px var(--acb)" : "none"
    }
  }, cell.label))))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 2,
      display: "flex",
      justifyContent: "space-between",
      paddingLeft: 118,
      fontFamily: "var(--font-mono)",
      fontSize: 9.5,
      color: "var(--ink-4)"
    }
  }, /*#__PURE__*/React.createElement("span", null, "lower"), /*#__PURE__*/React.createElement("span", null, "as specced (ringed)"), /*#__PURE__*/React.createElement("span", null, "higher"))) : /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      height: 64,
      alignItems: "center",
      fontFamily: "var(--font-mono)",
      fontSize: 12,
      color: "var(--ink-4)"
    }
  }, "sweep ran \u2014 treat every cell as anecdote below the evidence bar"), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 10,
      fontSize: 14,
      color: "var(--ink-2)"
    }
  }, hp.notes[3])));
}
function TradeLog({
  run
}) {
  const [open, setOpen] = React.useState(false);
  const [showSkipped, setShowSkipped] = React.useState(false);
  const filled = run.trades.filter(t => !t.skip);
  const skipped = run.trades.filter(t => t.skip);
  const Row = ({
    t
  }) => /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "84px 58px 1.3fr 74px 1fr",
      alignItems: "baseline",
      gap: 10,
      borderTop: "1px solid var(--grid)",
      padding: "8px 0",
      fontFamily: "var(--font-mono)",
      fontSize: 13,
      opacity: t.skip ? 0.55 : 1
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--ink-4)"
    }
  }, t.d), /*#__PURE__*/React.createElement("span", {
    style: {
      color: t.a === "SKIP" ? "var(--ink-4)" : t.a === "OPEN" ? "var(--ink)" : "var(--ink-3)",
      fontStyle: t.a === "SKIP" ? "italic" : "normal"
    }
  }, t.a), /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--ink-3)"
    }
  }, t.det), /*#__PURE__*/React.createElement("span", {
    style: {
      textAlign: "right",
      color: t.plSign === "pos" ? "var(--pl-pos)" : t.plSign === "neg" ? "var(--pl-neg)" : "var(--ink-3)"
    }
  }, t.pl), /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--ink-4)"
    }
  }, t.n));
  return /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement("button", {
    onClick: () => setOpen(!open),
    style: {
      display: "flex",
      width: "100%",
      alignItems: "center",
      gap: 10,
      borderRadius: open ? "14px 14px 0 0" : 14,
      border: "1px solid var(--line)",
      background: "var(--panel)",
      padding: "14px 20px",
      textAlign: "left",
      fontFamily: "var(--font-mono)",
      fontSize: 13.5,
      color: "var(--ink-3)"
    }
  }, /*#__PURE__*/React.createElement("span", null, open ? "▾" : "▸"), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1
    }
  }, run.tradeHeader), /*#__PURE__*/React.createElement(DS.Hint, {
    align: "right",
    text: "Every simulated fill, priced at bid/ask plus slippage \u2014 never mid. Skipped entries are listed with the reason each was refused."
  })), open && /*#__PURE__*/React.createElement("div", {
    style: {
      borderRadius: "0 0 12px 12px",
      border: "1px solid var(--line)",
      borderTop: "none",
      background: "var(--panel-deep)",
      padding: "6px 16px 10px"
    }
  }, filled.map((t, i) => /*#__PURE__*/React.createElement(Row, {
    key: i,
    t: t
  })), skipped.length > 0 && /*#__PURE__*/React.createElement("button", {
    onClick: () => setShowSkipped(!showSkipped),
    style: {
      marginTop: 4,
      display: "flex",
      width: "100%",
      alignItems: "center",
      gap: 8,
      borderTop: "1px solid var(--grid)",
      padding: "10px 0",
      textAlign: "left",
      fontFamily: "var(--font-mono)",
      fontSize: 11.5,
      color: "var(--ink-4)"
    }
  }, /*#__PURE__*/React.createElement("span", null, showSkipped ? "▾" : "▸"), /*#__PURE__*/React.createElement("span", null, skipped.length, " skipped entries \u2014 with reasons")), showSkipped && skipped.map((t, i) => /*#__PURE__*/React.createElement(Row, {
    key: "s" + i,
    t: t
  }))));
}
function AskBar({
  run
}) {
  const [text, setText] = React.useState("");
  const [answer, setAnswer] = React.useState(null);
  const ask = () => {
    if (!text.trim()) return;
    setAnswer("Grounded Q&A uses only this run's computed stats — in the live product your question (\u201C" + text.trim() + "\u201D) is answered from the " + run.trades.length + "-trade record, or refused if the number wasn't computed.");
    setText("");
  };
  return /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 14
    }
  }, answer && /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 10,
      borderRadius: 14,
      border: "1px solid var(--line)",
      background: "var(--panel)",
      padding: "14px 18px",
      fontSize: 14,
      lineHeight: 1.6,
      color: "var(--ink-2)"
    }
  }, answer), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8,
      borderRadius: 14,
      border: "1px solid var(--line-soft)",
      background: "var(--panel)",
      padding: "6px 6px 6px 18px"
    }
  }, /*#__PURE__*/React.createElement("input", {
    value: text,
    onChange: e => setText(e.target.value),
    onKeyDown: e => {
      if (e.key === "Enter") ask();
    },
    placeholder: "ask about this result \u2014 answers use only this run's computed stats\u2026",
    style: {
      flex: 1,
      fontSize: 14.5,
      color: "var(--ink)"
    }
  }), /*#__PURE__*/React.createElement(DS.Button, {
    variant: "dark",
    onClick: ask
  }, "ask")));
}
function ResultsScreen({
  run,
  onEditSpec,
  onNew,
  onBack,
  backLabel
}) {
  const dim = run.unblessed ? {
    opacity: 0.55
  } : null;
  return /*#__PURE__*/React.createElement("div", null, onBack && /*#__PURE__*/React.createElement("button", {
    onClick: onBack,
    style: {
      marginBottom: 18,
      fontSize: 13,
      color: "var(--ink-4)"
    }
  }, "\u2039 ", backLabel || "back to library"), /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 16,
      display: "flex",
      alignItems: "flex-start",
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h1", {
    style: {
      margin: "0 0 6px",
      fontSize: 21,
      fontWeight: 650
    }
  }, run.name), /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: "var(--font-mono)",
      fontSize: 11.5,
      color: "var(--ink-4)"
    }
  }, run.meta)), /*#__PURE__*/React.createElement("div", {
    style: {
      marginLeft: "auto",
      display: "flex",
      flexShrink: 0,
      gap: 8
    }
  }, onEditSpec && /*#__PURE__*/React.createElement(DS.Button, {
    variant: "secondary",
    onClick: onEditSpec
  }, "\u2039 edit spec"), onNew && /*#__PURE__*/React.createElement(DS.Button, {
    variant: "secondary",
    onClick: onNew
  }, "+ new analysis"))), /*#__PURE__*/React.createElement(DS.VerdictBlock, {
    verdict: run.verdict
  }), run.unblessed && /*#__PURE__*/React.createElement("div", {
    style: {
      margin: "14px 0 -6px",
      fontFamily: "var(--font-mono)",
      fontSize: 10.5,
      letterSpacing: ".14em",
      color: "var(--ink-4)"
    }
  }, "UNBLESSED OUTPUT \u2014 NUMBERS SHOWN, BLESSING WITHHELD (* = below the evidence bar)"), /*#__PURE__*/React.createElement("div", {
    style: dim
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 20,
      display: "grid",
      gridTemplateColumns: "repeat(6, 1fr)",
      gap: 12
    }
  }, run.mtiles.map((m, i) => /*#__PURE__*/React.createElement(DS.MetricTile, {
    key: m.l,
    value: m.v,
    label: m.l,
    neg: m.neg,
    hintAlign: i >= 4 ? "right" : "center",
    hint: {
      CAGR: "How fast the account grew per year, on average.",
      SHARPE: "Return earned per unit of risk taken. Under ~1 is weak.",
      SORTINO: "Like Sharpe, but only downside swings count as risk.",
      "MAX DD": "The deepest peak-to-trough loss the account suffered.",
      "WIN RATE": "Share of closed trades that made money.",
      "P·FACTOR": "Total gains divided by total losses. Above 1 = net profitable."
    }[m.l.replace(/\*$/, "")]
  }))), /*#__PURE__*/React.createElement(EquityChart, {
    run: run
  }), /*#__PURE__*/React.createElement(HonestyPanels, {
    run: run
  }), run.recommendations.length > 0 && /*#__PURE__*/React.createElement(DS.Panel, {
    style: {
      marginTop: 14
    },
    title: /*#__PURE__*/React.createElement("span", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: 8
      }
    }, "WHAT WOULD IMPROVE IT \u2014 COMPUTED FROM THIS RUN ", /*#__PURE__*/React.createElement(DS.Hint, {
      text: "Each suggestion comes from sweeps we actually ran on your strategy \u2014 never opinion. Acting on one starts a new trial the deflated Sharpe counts against you."
    }))
  }, /*#__PURE__*/React.createElement("ul", {
    style: {
      margin: 0,
      padding: 0,
      listStyle: "none",
      display: "flex",
      flexDirection: "column",
      gap: 10
    }
  }, run.recommendations.map((rec, i) => /*#__PURE__*/React.createElement("li", {
    key: i,
    style: {
      display: "flex",
      gap: 12,
      fontSize: 14.5,
      lineHeight: 1.6,
      color: "var(--ink-2)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--font-mono)",
      color: "var(--ac)"
    }
  }, String(i + 1).padStart(2, "0")), /*#__PURE__*/React.createElement("span", null, rec)))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 12,
      borderTop: "1px solid var(--grid)",
      paddingTop: 10,
      fontFamily: "var(--font-mono)",
      fontSize: 10.5,
      color: "var(--ink-4)"
    }
  }, "backtest-fit observations, not trading advice \u2014 every change re-enters the gauntlet as a new trial")), /*#__PURE__*/React.createElement(TradeLog, {
    run: run
  }), /*#__PURE__*/React.createElement(AskBar, {
    run: run
  })), /*#__PURE__*/React.createElement(DS.Disclaimer, null));
}
Object.assign(window, {
  ResultsScreen
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/app/ResultsScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/app/SettingsScreen.jsx
try { (() => {
/* Settings — appearance is LIVE (theme + accent swap the whole palette via
   data attributes); costs/verbiage/status are faithful statics. */
const setDS = window.SkepticDesignSystem_68b393;
const ACCENT_PREVIEW = {
  cyan: {
    dark: "rgb(63 193 207)",
    light: "rgb(13 125 138)",
    label: "Cyan"
  },
  sage: {
    dark: "rgb(156 204 163)",
    light: "rgb(58 122 72)",
    label: "Sage"
  },
  lavender: {
    dark: "rgb(178 164 235)",
    light: "rgb(100 84 200)",
    label: "Lavender"
  },
  rose: {
    dark: "rgb(232 166 180)",
    light: "rgb(176 71 96)",
    label: "Rose"
  }
};
function Seg({
  options,
  value,
  onChange,
  wide
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "inline-flex",
      gap: 2,
      borderRadius: 11,
      border: "1px solid var(--line-soft)",
      padding: 3
    }
  }, options.map(o => /*#__PURE__*/React.createElement("button", {
    key: o,
    onClick: () => onChange(o),
    style: {
      borderRadius: 9,
      padding: wide ? "8px 20px" : "6px 14px",
      fontSize: wide ? 14 : 13,
      fontWeight: 600,
      textTransform: "capitalize",
      background: value === o ? "var(--raised-3)" : "transparent",
      color: value === o ? "var(--ink)" : "var(--ink-4)"
    }
  }, o === "market" ? "market hours" : o)));
}
function CostField({
  label,
  suffix,
  value
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      gap: 16,
      fontSize: 14.5
    }
  }, /*#__PURE__*/React.createElement("span", null, label), /*#__PURE__*/React.createElement("span", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("input", {
    defaultValue: value,
    style: {
      width: 92,
      borderRadius: 9,
      border: "1px solid var(--line)",
      background: "var(--panel-deep)",
      padding: "6px 12px",
      textAlign: "right",
      fontFamily: "var(--font-mono)",
      fontSize: 14,
      color: "var(--ink)"
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      width: 120,
      fontFamily: "var(--font-mono)",
      fontSize: 12,
      color: "var(--ink-4)"
    }
  }, suffix)));
}
function StatusRow({
  label,
  value,
  dim
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      gap: 16,
      fontSize: 14.5
    }
  }, /*#__PURE__*/React.createElement("span", null, label), /*#__PURE__*/React.createElement("span", {
    style: {
      textAlign: "right",
      fontFamily: "var(--font-mono)",
      fontSize: 13,
      color: dim ? "var(--ink-4)" : "var(--ink)"
    }
  }, value));
}
function SettingsScreen({
  settings,
  onSettings
}) {
  const resolved = settings.resolved;
  const [verbiage, setVerbiage] = React.useState("institutional");
  return /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 860
    }
  }, /*#__PURE__*/React.createElement("h1", {
    style: {
      margin: "0 0 26px",
      fontFamily: "var(--font-serif)",
      fontSize: 32,
      fontWeight: 500
    }
  }, "Settings"), /*#__PURE__*/React.createElement(setDS.Panel, {
    style: {
      marginBottom: 14
    },
    title: "APPEARANCE"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 14.5
    }
  }, "Mode"), /*#__PURE__*/React.createElement(Seg, {
    options: ["market", "light", "dark"],
    value: settings.theme,
    onChange: t => onSettings({
      theme: t
    })
  })), settings.theme === "market" && /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      fontSize: 12.5,
      lineHeight: 1.55,
      color: "var(--ink-4)"
    }
  }, "Market Hours follows the clock \u2014 light from 8am to 6pm New York time, dark after the close. Right now it's showing ", /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--font-mono)",
      color: "var(--ink-2)"
    }
  }, resolved), "."), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 14.5
    }
  }, "Accent"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 10
    }
  }, Object.keys(ACCENT_PREVIEW).map(a => /*#__PURE__*/React.createElement("button", {
    key: a,
    onClick: () => onSettings({
      accent: a
    }),
    title: ACCENT_PREVIEW[a].label,
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8,
      borderRadius: 999,
      border: "1px solid " + (settings.accent === a ? "var(--acb)" : "var(--line)"),
      background: settings.accent === a ? "var(--acd)" : "transparent",
      padding: "6px 12px",
      fontSize: 13,
      color: settings.accent === a ? "var(--ink)" : "var(--ink-4)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-block",
      height: 14,
      width: 14,
      borderRadius: "50%",
      background: ACCENT_PREVIEW[a][resolved]
    }
  }), ACCENT_PREVIEW[a].label)))), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      fontSize: 12.5,
      lineHeight: 1.55,
      color: "var(--ink-4)"
    }
  }, "The accent is the trust hue \u2014 verdicts, trust bands and controls. It never colors profit or loss, in either mode."))), /*#__PURE__*/React.createElement(setDS.Panel, {
    style: {
      marginBottom: 14
    },
    title: "COSTS \u2014 APPLIED TO EVERY NEW RUN"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 14
    }
  }, /*#__PURE__*/React.createElement(CostField, {
    label: "Commission",
    suffix: "$ / contract / side",
    value: "0.65"
  }), /*#__PURE__*/React.createElement(CostField, {
    label: "Slippage \u2014 buys",
    suffix: "\xD7 half-spread",
    value: "0.85"
  }), /*#__PURE__*/React.createElement(CostField, {
    label: "Slippage \u2014 sells",
    suffix: "\xD7 half-spread",
    value: "0.90"
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12.5,
      lineHeight: 1.55,
      color: "var(--ink-4)"
    }
  }, "Buys fill toward the ask, sells toward the bid, plus these fractions of the half-spread. The defaults are measured, not assumed. Mid fills (0) are banned by design."))), /*#__PURE__*/React.createElement(setDS.Panel, {
    style: {
      marginBottom: 14
    },
    title: "VERBIAGE COMPLEXITY"
  }, /*#__PURE__*/React.createElement(Seg, {
    wide: true,
    options: ["institutional", "retail"],
    value: verbiage,
    onChange: setVerbiage
  }), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "12px 0 0",
      fontSize: 13.5,
      lineHeight: 1.6,
      color: "var(--ink-3)"
    }
  }, verbiage === "institutional" ? "Full quantitative language — Sharpe ratios, percentiles, out-of-sample splits, deflated statistics. For readers who live in this vocabulary." : "Plain English everywhere — verdicts, findings and recommendations rewritten for an everyday trader. Same numbers, same honesty, no jargon.")), /*#__PURE__*/React.createElement(setDS.Panel, {
    style: {
      marginBottom: 14
    },
    title: "SYSTEM STATUS"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 10
    }
  }, /*#__PURE__*/React.createElement(StatusRow, {
    label: "Backend",
    value: "ok \u2713"
  }), /*#__PURE__*/React.createElement(StatusRow, {
    label: "Data lake (R2)",
    value: "configured \u2713"
  }), /*#__PURE__*/React.createElement(StatusRow, {
    label: "Backtest engine + gauntlet",
    value: "ready"
  }), /*#__PURE__*/React.createElement(StatusRow, {
    label: "NL parser",
    value: "ready"
  }), /*#__PURE__*/React.createElement(StatusRow, {
    label: "Numeric validation",
    value: "on \u2014 no un-computed numbers"
  }), /*#__PURE__*/React.createElement(StatusRow, {
    label: "Seeds",
    value: "fixed & logged per run",
    dim: true
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      borderRadius: 14,
      border: "1px dashed var(--line-hover)",
      padding: 20,
      fontSize: 13,
      lineHeight: 1.65,
      color: "var(--ink-3)"
    }
  }, "Skeptic is a research instrument. It produces no recommendations to buy or sell any security. Backtests are computed on approximate, self-collected data and systematically overstate real-world results. Past performance does not predict future results."));
}
Object.assign(window, {
  SettingsScreen
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/app/SettingsScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/app/demo-data.js
try { (() => {
/* Skeptic UI kit — deterministic demo fixtures (shapes mirror lib/demo.ts). */
(function () {
  function equitySeries() {
    const pts = [];
    let v = 25000;
    for (let i = 0; i < 140; i++) {
      const drift = i < 55 ? 62 : i < 78 ? -88 : i < 112 ? 74 : 40;
      const wobble = Math.sin(i * 1.7) * 120 + Math.sin(i * 0.53) * 210;
      v = Math.max(16000, v + drift + wobble * 0.18);
      const y = 2020 + Math.floor(i / 23);
      const m = String(1 + i % 23 * 0.5 | 0).padStart(2, "0");
      pts.push({
        t: y + "-" + (m === "00" ? "01" : m) + "-15",
        v: Math.round(v)
      });
    }
    return pts;
  }
  const equity = equitySeries();
  const drawdown = function () {
    let peak = -Infinity;
    return equity.map(function (p) {
      peak = Math.max(peak, p.v);
      return {
        t: p.t,
        v: (peak - p.v) / peak * 100
      };
    });
  }();
  function toPoints(series, W, H, pad, invert) {
    const vs = series.map(function (p) {
      return p.v;
    });
    const lo = Math.min.apply(null, vs),
      hi = Math.max.apply(null, vs),
      span = hi - lo || 1;
    return series.map(function (p, i) {
      const x = i / (series.length - 1) * W;
      const f = (p.v - lo) / span;
      const y = invert ? pad + f * (H - 2 * pad) : pad + (1 - f) * (H - 2 * pad);
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
  }
  function mcPath(seed, end) {
    const pts = [];
    let v = 30;
    for (let i = 0; i <= 40; i++) {
      v += (end - 30) / 40 + Math.sin(i * seed) * 2.2;
      pts.push((i / 40 * 400).toFixed(1) + "," + Math.min(96, Math.max(4, 100 - v)).toFixed(1));
    }
    return pts.join(" ");
  }
  const verdictGraded = {
    headline: "The edge is real but thin — and it only earns its keep in calm markets.",
    survived: "SURVIVED 4 OF 5 ATTACKS",
    band: {
      left: "38%",
      width: "26%"
    },
    marker: "52%",
    chips: ["OOS: holds", "walk-forward: 7/9 windows", "Monte Carlo: p05 > $0", "sensitivity: plateau", "luck: unlikely"],
    evidence: ["Out-of-sample Sharpe keeps 71% of in-sample (0.93 vs 1.31).", "Wins in 7 of 9 walk-forward windows; no single window carries the total.", "Median reshuffle still ends positive — sequence luck isn't the story."],
    breaks: ["VIX above 28 flips expectancy negative — the 2024-08 spike alone erased 4 months of gains.", "Fills assume displayed size; 3% of fills exceeded it.", "Below 45 DTE the plateau narrows toward a cliff."],
    caveat: "Standing caveats: 6.2 years of data · fills at bid/ask plus measured slippage, never mid · every re-run of this family raises the luck bar · backtests overstate live results."
  };
  const verdictRefusal = {
    refusal: true,
    headline: "Not enough evidence to grade this. That's the honest answer.",
    survived: "VERDICT WITHHELD",
    refusalBody: "The spec is valid and the engine ran it — but only 9 trades closed inside the data window. Below your 15-trade evidence bar a verdict would be statistical theater, so this run ships unblessed: numbers shown, blessing withheld.",
    refusalUnlock: "unlocks at ~15 trades — about 9 more weeks of QQQ chain collection"
  };
  const runFresh = {
    id: "r1",
    name: "30Δ weekly SPY put",
    meta: "short put · SPY · weekly · 50% profit / 21 DTE exit · ran Jul 16 ’26 · seed 811",
    verdict: verdictGraded,
    mtiles: [{
      v: "+18.4%",
      l: "CAGR"
    }, {
      v: "1.31",
      l: "SHARPE"
    }, {
      v: "1.74",
      l: "SORTINO"
    }, {
      v: "−31.7%",
      l: "MAX DD",
      neg: true
    }, {
      v: "68%",
      l: "WIN RATE"
    }, {
      v: "1.62",
      l: "P·FACTOR"
    }],
    equity: equity,
    drawdown: drawdown,
    equityPoints: toPoints(equity, 860, 200, 14, false),
    drawdownPoints: toPoints(drawdown, 860, 54, 5, true),
    oosShadeX: 602,
    startLabel: "$25k start · net of costs",
    honesty: {
      isSharpe: "1.31",
      oosSharpe: "0.93",
      bar1: "86%",
      bar2: "61%",
      notes: ["Keeps 71% of its in-sample Sharpe on unseen data — above the 50% curve-fit line.", "7 of 9 windows positive; the two losers straddle the 2024-08 volatility spike.", "5th percentile still ends above $0 — ruin needs bad luck AND bad sizing.", "A plateau, not a cliff: ±20% nudges keep 80%+ of the result. Delta is the sensitive dial."],
      wf: [{
        h: 44,
        pos: true,
        t: "Jan–Feb ’25 · +$1,120 · 9 trades"
      }, {
        h: 30,
        pos: true,
        t: "Mar–Apr ’25 · +$760"
      }, {
        h: 18,
        pos: false,
        t: "May–Jun ’25 · −$410"
      }, {
        h: 52,
        pos: true,
        t: "Jul–Aug ’25 · +$1,380"
      }, {
        h: 26,
        pos: true,
        t: "Sep–Oct ’25 · +$610"
      }, {
        h: 34,
        pos: false,
        t: "Nov–Dec ’25 · −$980"
      }, {
        h: 40,
        pos: true,
        t: "Jan–Feb ’26 · +$1,050"
      }, {
        h: 22,
        pos: true,
        t: "Mar–Apr ’26 · +$490"
      }, {
        h: 36,
        pos: true,
        t: "May–Jun ’26 · +$900"
      }]
    },
    mc: {
      p95: mcPath(0.9, 78),
      p50: mcPath(1.7, 58),
      p05: mcPath(2.6, 34)
    },
    mcTerm: {
      p95: "$61,400",
      p50: "$44,900",
      p05: "$27,100"
    },
    sensitivity: [{
      name: "delta",
      base: 3,
      cells: [{
        label: ".15",
        o: 0.25
      }, {
        label: ".20",
        o: 0.45
      }, {
        label: ".25",
        o: 0.62
      }, {
        label: ".30",
        o: 0.78
      }, {
        label: ".35",
        o: 0.66
      }, {
        label: ".40",
        o: 0.4
      }, {
        label: ".45",
        o: 0.22
      }]
    }, {
      name: "DTE",
      base: 3,
      cells: [{
        label: "21",
        o: 0.3
      }, {
        label: "28",
        o: 0.55
      }, {
        label: "35",
        o: 0.7
      }, {
        label: "45",
        o: 0.76
      }, {
        label: "52",
        o: 0.72
      }, {
        label: "60",
        o: 0.65
      }, {
        label: "75",
        o: 0.5
      }]
    }, {
      name: "profit tgt",
      base: 3,
      cells: [{
        label: "25%",
        o: 0.5
      }, {
        label: "35%",
        o: 0.62
      }, {
        label: "45%",
        o: 0.72
      }, {
        label: "50%",
        o: 0.78
      }, {
        label: "60%",
        o: 0.7
      }, {
        label: "75%",
        o: 0.55
      }, {
        label: "90%",
        o: 0.35
      }]
    }],
    recommendations: ["Raising the profit target from 50% to 60% kept 96% of CAGR while cutting time-in-trade 22% — the sweep actually ran it.", "A 40-delta version earned more but its OOS Sharpe fell to 0.51 — the extra yield is curve-fit; stay at 30.", "Skipping entries when VIX > 28 removed both losing walk-forward windows at the cost of 11% of trades."],
    tradeHeader: "214 trades · 146 wins · 61 losses · 7 skipped — expand the log",
    trades: [{
      d: "Mar 4 ’26",
      a: "OPEN",
      det: "sold 1× SPY 512P 45 DTE @ 2.31 (bid 2.29/ask 2.35)",
      pl: "",
      n: "credit $231"
    }, {
      d: "Mar 18 ’26",
      a: "CLOSE",
      det: "bought back @ 1.12 — 50% profit target",
      pl: "+$115",
      plSign: "pos",
      n: "held 14d"
    }, {
      d: "Mar 25 ’26",
      a: "OPEN",
      det: "sold 1× SPY 508P 44 DTE @ 2.44",
      pl: "",
      n: "credit $244"
    }, {
      d: "Apr 8 ’26",
      a: "SKIP",
      det: "spread 31% of mid — fill would be fantasy",
      pl: "",
      n: "refused",
      skip: true
    }, {
      d: "Apr 15 ’26",
      a: "CLOSE",
      det: "bought back @ 4.90 — 21 DTE time exit",
      pl: "−$246",
      plSign: "neg",
      n: "vol spike"
    }, {
      d: "Apr 22 ’26",
      a: "OPEN",
      det: "sold 1× SPY 501P 46 DTE @ 2.18",
      pl: "",
      n: "credit $218"
    }, {
      d: "May 6 ’26",
      a: "CLOSE",
      det: "bought back @ 1.05 — 50% profit target",
      pl: "+$113",
      plSign: "pos",
      n: "held 14d"
    }, {
      d: "May 13 ’26",
      a: "SKIP",
      det: "zero bids at the 30Δ strike",
      pl: "",
      n: "refused",
      skip: true
    }]
  };
  const runRefusal = {
    id: "r4",
    name: "QQQ 0DTE fade",
    meta: "call credit spread · QQQ · daily · ran Jul 12 ’26 · seed 402",
    verdict: verdictRefusal,
    mtiles: [{
      v: "+41.0%*",
      l: "CAGR*"
    }, {
      v: "2.10*",
      l: "SHARPE*"
    }, {
      v: "—",
      l: "SORTINO"
    }, {
      v: "−9.4%*",
      l: "MAX DD*",
      neg: true
    }, {
      v: "89%*",
      l: "WIN RATE*"
    }, {
      v: "3.1*",
      l: "P·FACTOR*"
    }],
    unblessed: true,
    equity: equity.slice(0, 30),
    drawdown: drawdown.slice(0, 30),
    equityPoints: toPoints(equity.slice(0, 30), 860, 200, 14, false),
    drawdownPoints: toPoints(drawdown.slice(0, 30), 860, 54, 5, true),
    oosShadeX: 860,
    startLabel: "$25k start · net of costs",
    honesty: {
      isSharpe: "2.10*",
      oosSharpe: "—",
      bar1: "92%",
      bar2: "4%",
      notes: ["Too few closed trades for the split to mean anything.", "One window; nothing to compare.", "Reshuffling 9 trades proves nothing — the fan is decorative at this sample.", "Sweep ran; treat every cell as anecdote below the evidence bar."],
      wf: [{
        h: 40,
        pos: true,
        t: "Jun ’26 · +$310 · 9 trades"
      }]
    },
    mc: {
      p95: mcPath(0.8, 70),
      p50: mcPath(1.5, 52),
      p05: mcPath(2.2, 30)
    },
    mcTerm: {
      p95: "$29,800*",
      p50: "$27,400*",
      p05: "$24,100*"
    },
    sensitivity: [],
    recommendations: [],
    tradeHeader: "9 trades · 8 wins · 1 loss · 2 skipped — expand the log",
    trades: [{
      d: "Jul 1 ’26",
      a: "OPEN",
      det: "sold QQQ 462/467 call spread @ 1.10",
      pl: "",
      n: "credit $110"
    }, {
      d: "Jul 1 ’26",
      a: "CLOSE",
      det: "expired worthless",
      pl: "+$110",
      plSign: "pos",
      n: "0 DTE"
    }, {
      d: "Jul 2 ’26",
      a: "SKIP",
      det: "minute data gap 09:31–09:47 — honest hole",
      pl: "",
      n: "refused",
      skip: true
    }]
  };
  const library = [{
    id: "r1",
    name: "30Δ weekly SPY put",
    meta: "short put · 214 trades · ran Jul 16 ’26",
    band: {
      left: "38%",
      width: "26%"
    },
    marker: "52%",
    quote: "“Real but thin — calm markets only.”"
  }, {
    id: "r2",
    name: "16Δ SPY condor 45 DTE",
    meta: "iron condor · 121 trades · ran Jul 9 ’26",
    band: {
      left: "55%",
      width: "24%"
    },
    marker: "64%",
    quote: "“Survived every attack. Small edge, honestly earned.”"
  }, {
    id: "r3",
    name: "Fade-the-rally QQQ spread",
    meta: "call credit spread · 96 trades · ran Jul 2 ’26",
    band: {
      left: "8%",
      width: "22%"
    },
    marker: "14%",
    quote: "“In-sample beauty, out-of-sample noise. Curve-fit.”"
  }, {
    id: "r4",
    name: "QQQ 0DTE fade",
    meta: "call credit spread · 9 trades · ran Jul 12 ’26",
    withheld: true,
    quote: "“Not enough evidence to grade. Unblessed by design.”"
  }, {
    id: "r5",
    name: "Crash-insurance 10Δ put",
    meta: "long put · running now",
    running: true,
    stage: 3
  }, {
    id: "r6",
    name: "Covered-call monthly roll",
    meta: "covered call · 74 trades · ran Jun 27 ’26",
    band: {
      left: "30%",
      width: "30%"
    },
    marker: "42%",
    quote: "“Income is real; the drawdown protection is a story.”"
  }];
  const runsById = {
    r1: runFresh,
    r4: runRefusal
  };
  window.SkepticDemo = {
    runFresh: runFresh,
    runRefusal: runRefusal,
    library: library,
    runsById: runsById
  };
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/app/demo-data.js", error: String((e && e.message) || e) }); }

__ds_ns.Composer = __ds_scope.Composer;

__ds_ns.CoverageChip = __ds_scope.CoverageChip;

__ds_ns.PresetChip = __ds_scope.PresetChip;

__ds_ns.QuestionCard = __ds_scope.QuestionCard;

__ds_ns.ThinkingIndicator = __ds_scope.ThinkingIndicator;

__ds_ns.Button = __ds_scope.Button;

__ds_ns.DemoBadge = __ds_scope.DemoBadge;

__ds_ns.Disclaimer = __ds_scope.Disclaimer;

__ds_ns.Hint = __ds_scope.Hint;

__ds_ns.MetricTile = __ds_scope.MetricTile;

__ds_ns.Panel = __ds_scope.Panel;

__ds_ns.PulsingDots = __ds_scope.PulsingDots;

__ds_ns.GauntletProgress = __ds_scope.GauntletProgress;

__ds_ns.NavRail = __ds_scope.NavRail;

__ds_ns.TrustBand = __ds_scope.TrustBand;

__ds_ns.VerdictBlock = __ds_scope.VerdictBlock;

})();
