"use client";

/**
 * "How this was built" (UX Chunk B) — the run's provenance record (Chunk A)
 * rendered as the setup story, top to bottom: the initial prompt (styled as
 * the user's message) → each clarifying question with the chosen answer
 * highlighted → the confirmed spec as a decision grid → the run-mechanics
 * line. Read-only v1.
 *
 * Two record shapes arrive behind one key (the Chunk A contract): STORED
 * records carry confirmed.draft (the SpecDraft exactly as confirmed) plus
 * source and mechanics.build; DERIVED records (runs predating the column)
 * carry confirmed.boxes (a spec_json projection, marked derived), no
 * source/build, and NO conversation — it was never stored and is never
 * invented, so a disclosed line stands where the conversation would be.
 *
 * Palette: neutral inks + the trust accent only — never P/L or verdict
 * colors (CLAUDE.md rail).
 */

import Link from "next/link";

import { pinLabel, shortDate } from "@/lib/format";
import type { ProvenanceEvent, RunPayload, RunProvenance, SpecDraft } from "@/lib/types";
import { STRUCTURE_LABEL, type Structure } from "@/lib/types";

import { PANEL } from "@/components/results/panel";

// grid cell labels — smaller than the shared PANEL_TITLE by design
const LABEL = "font-mono text-[10.5px] font-medium tracking-[.12em] text-ink-4";

// module-level: formatter construction is expensive and this renders once
// per exchange card
const CLOCK_FMT = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit", minute: "2-digit", hour12: false,
});

function clockTime(iso?: string): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return CLOCK_FMT.format(d);
}

function duration(seconds: number): string {
  const s = Math.round(seconds);
  if (s < 1) return "<1s";
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

// --------------------------------------------------------------- Q&A pairing
interface Exchange {
  question?: ProvenanceEvent;
  answer?: ProvenanceEvent;
  /** index of `answer` in the stored conversation. V-200's labels anchor here
   *  rather than on a position in this paired list, so the server's pairing and
   *  this one never have to agree for a marker to land on the right card. */
  answerIndex?: number;
}

type Superseded = NonNullable<RunProvenance["reconciliation"]>["labels"][number];

/** Pair answers to questions by event id, preserving chronology. A round's
 * questions arrive together (one asked_at) and the answers follow — each
 * answer attaches to the EARLIEST unanswered question sharing its id, which
 * is right in both real shapes: within one round answers arrive in question
 * order (a latest-first match would reverse them when ids collide or are
 * empty), and a re-asked id in a later round finds its own round's question
 * because the earlier one is already answered. An answer with no matching
 * question still renders — the record is the truth. */
function pairConversation(events: ProvenanceEvent[]): Exchange[] {
  const out: Exchange[] = [];
  events.forEach((ev, index) => {
    if (ev.kind === "question") {
      out.push({ question: ev });
      return;
    }
    const open = out.find((x) => x.question?.id === ev.id && !x.answer);
    if (open) {
      open.answer = ev;
      open.answerIndex = index;
    } else {
      out.push({ answer: ev, answerIndex: index });
    }
  });
  return out;
}

const showValue = (v: unknown) =>
  v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v);

/** V-208: the field's name, with its path as a secondary.
 *
 *  ONE component for both surfaces that name a field — the SUPERSEDED marker and
 *  WHAT CHANGED — so the reader connects them instead of translating between a
 *  prose label in one place and a path in the other. The label comes from the
 *  single server-side table; when a path has no entry the path itself is the
 *  label, which is correct rather than degraded, and the gap was already counted
 *  server-side so the table's holes report themselves.
 *
 *  The path stays visible because it is the authority: it is what the diff
 *  contract pins (V-164), what the reconciler matched on, and what someone
 *  reading a stored record or an API payload will see. Dimmed and smaller, so it
 *  is available without being what you read first. */
function FieldName({ field, label }: { field: string; label?: string }) {
  if (!label) return <span className="font-mono text-[11.5px] text-ink-3">{field}</span>;
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-1.5" title={field}>
      <span className="text-[13px] text-ink-2">{label}</span>
      <span className="font-mono text-[10.5px] text-ink-4">{field}</span>
    </span>
  );
}

function ValueChange({ parent, variant }: { parent: unknown; variant: unknown }) {
  return (
    <>
      <span className="font-mono text-[12.5px] text-ink-3 line-through decoration-line">
        {showValue(parent)}
      </span>
      <span className="font-mono text-[11px] text-ink-4">→</span>
      <span className="font-mono text-[12.5px] font-semibold text-ink">
        {showValue(variant)}
      </span>
    </>
  );
}

function SupersededLine({ label }: { label: Superseded }) {
  return (
    <div className="mt-3 border-t border-trust-border pt-2.5">
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
        <span className="font-mono text-[10.5px] font-medium tracking-[.12em] text-ink-4">
          SUPERSEDED ON THIS RUN
        </span>
        <FieldName field={label.field} label={label.label} />
        <ValueChange parent={label.parent} variant={label.variant} />
      </div>
    </div>
  );
}

function AnswerChip({ text, typed }: { text: string; typed?: boolean }) {
  return (
    <span className="rounded-full bg-trust px-3.5 py-[6px] text-[13px] font-semibold text-on-accent">
      {typed ? `“${text}”` : text}
    </span>
  );
}

function ExchangeCard({
  x,
  carried = false,
  superseded,
}: {
  x: Exchange;
  carried?: boolean;
  superseded?: Superseded;
}) {
  const q = x.question;
  const answer = x.answer?.answer;
  if (!q) {
    // an answer the record holds without its question — show it honestly
    return (
      <div className="flex flex-col gap-2.5">
        <div className="flex items-center gap-2.5">
          <span className={LABEL}>ANSWERED</span>
          {answer && <AnswerChip text={answer} typed />}
        </div>
        {superseded && <SupersededLine label={superseded} />}
      </div>
    );
  }
  const options = q.options ?? [];
  const chosen = answer !== undefined && options.includes(answer) ? answer : null;
  const time = clockTime(x.answer?.answered_at ?? q.asked_at);
  return (
    <div className="rounded-[14px] border border-trust-border bg-trust-dim px-5 py-4">
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <span className="font-mono text-[10.5px] font-medium tracking-[.12em] text-trust">
          {/* V-177/V-178: "IT ASKED — I DON'T GUESS" is present-tense fresh-run
              voice. Inside a carried block it reads as though the interview
              ran here, contradicting the header directly above it — and the
              cards are the visually dominant element, so a skimmer believes
              the cards. The eyebrow carries its share of the misattribution
              work rather than leaving it all to the header; A2's per-exchange
              marker is about which answers still apply, which is a different
              question from where they were asked. */}
          {carried ? "IT ASKED ON THE ORIGINAL RUN" : "IT ASKED — I DON’T GUESS"}
        </span>
        {time && <span className="font-mono text-[10.5px] text-ink-4">{time}</span>}
      </div>
      <div className="mb-3 text-[15.5px] font-semibold leading-snug">{q.question}</div>
      <div className={`flex flex-wrap items-center gap-2${superseded ? " opacity-60" : ""}`}>
        {options.map((opt) =>
          opt === chosen ? (
            <AnswerChip key={opt} text={opt} />
          ) : (
            <span
              key={opt}
              className="rounded-full border border-trust-border px-3.5 py-[6px] text-[13px] text-trust opacity-55"
            >
              {opt}
            </span>
          ),
        )}
        {answer !== undefined && !chosen && <AnswerChip text={answer} typed />}
        {answer === undefined && (
          <span className="font-mono text-[11.5px] text-ink-4">no answer recorded</span>
        )}
      </div>
      {/* The question and the original answer stay fully visible above: they
          are real history and the brief is explicit that they are not erased.
          The marker is additive, below them. */}
      {superseded && <SupersededLine label={superseded} />}
    </div>
  );
}

// ------------------------------------------------------------- decision grid
interface Box {
  label: string;
  value: string;
}

function fmtMoney(v: number): string {
  return `$${v.toLocaleString("en-US")}`;
}

function windowLabel(start?: string | null, end?: string | null): string {
  if (!start && !end) return "all history";
  return `${start ? shortDate(start) : "…"} → ${end ? shortDate(end) : "latest"}`;
}

/** Boxes from the STORED confirmed draft — the dials as the user saw them. */
function boxesFromDraft(draft: SpecDraft, costs?: Record<string, number>): Box[] {
  const out: (Box | null)[] = [
    { label: "TICKER", value: draft.ticker },
    { label: "STRUCTURE", value: STRUCTURE_LABEL[draft.structure] ?? draft.structure },
    { label: "STRIKE", value: draft.strikeLabel ?? `.${draft.strikeDelta}Δ` },
    { label: "TENOR", value: draft.dte === 0 ? "0 DTE" : `${draft.dte} DTE` },
    { label: "CADENCE", value: draft.cadence },
    { label: "SIZE", value: draft.size },
    draft.capital != null ? { label: "CAPITAL", value: fmtMoney(draft.capital) } : null,
    {
      label: "CLOCK",
      value: (draft.clock ?? "daily") + (draft.resolution === "finest" ? " · finest" : ""),
    },
    draft.intradayScan === "every_setup"
      ? { label: "SCANNING", value: "every setup" }
      : null,
    draft.window
      ? {
          label: "WINDOW",
          value:
            draft.window.kind === "custom"
              ? windowLabel(draft.window.start, draft.window.end)
              : draft.window.kind === "all"
                ? "all history"
                : draft.window.kind,
        }
      : null,
    draft.exit ? { label: "EXIT", value: draft.exit } : null,
    draft.conditionList?.length
      ? {
          label: "ENTRY LOGIC",
          value: draft.conditionList
            .map((c) => `${c.indicator}${c.period ? `(${c.period})` : ""} ${c.operator} ${c.value}`)
            .join(" · "),
        }
      : null,
    draft.ladder
      ? {
          label: "LADDER",
          value: `${draft.ladder.rungs.length} rungs · cap ${draft.ladder.cap}`,
        }
      : null,
    costs
      ? {
          label: "COSTS",
          value: `$${costs.commission_per_contract ?? "—"}/ct · slip ${costs.slippage_half_spread_fraction ?? "—"}/${costs.slippage_half_spread_fraction_sell ?? "—"}`,
        }
      : null,
  ];
  return out.filter((b): b is Box => b !== null);
}

/** Boxes from a DERIVED record's spec projection (snake_case, spec-shaped). */
function boxesFromSpec(boxes: Record<string, unknown>): Box[] {
  const b = boxes as {
    ticker?: string;
    structure?: string;
    legs?: { strike_selection?: { method?: string; value?: number } }[];
    expiration_selection?: { target_dte?: number };
    schedule?: { frequency?: string; day_of_week?: string | null; time_of_day?: string | null };
    conditions?: { indicator?: string; operator?: string; value?: number; period?: number }[];
    scale_in?: { rungs?: unknown[]; max_total_contracts?: number } | null;
    intraday_scan?: string | null;
    exit?: Record<string, unknown>;
    sizing?: { method?: string; value?: number };
    costs?: Record<string, number>;
    window?: { start?: string | null; end?: string | null };
    clock?: string;
    resolution?: string | null;
    initial_capital?: number;
  };

  const sel = b.legs?.[0]?.strike_selection;
  // multi-leg structures: the lead leg's strike is the dial the user chose
  // (same convention as the spec screen); a width leg is named beside it so
  // a spread never reads as a single-strike position
  const width = b.legs?.find(
    (l) => l.strike_selection?.method === "width_from_leg",
  )?.strike_selection?.value;
  const lead =
    sel?.method === "delta" && sel.value != null
      ? `.${Math.round(Math.abs(sel.value) * 100)}Δ`
      : sel?.method === "offset_pct" && sel.value != null
        ? `${Math.abs(sel.value * 100).toLocaleString("en-US")}% ${sel.value < 0 ? "below" : "above"} spot`
        : null;
  const strike = lead && width != null ? `${lead} · $${width} wide` : lead;

  const exitParts: string[] = [];
  const ex = b.exit ?? {};
  if (typeof ex.profit_target_pct === "number") exitParts.push(`${ex.profit_target_pct}% profit`);
  if (typeof ex.stop_loss_pct === "number") exitParts.push(`stop ${ex.stop_loss_pct}%`);
  if (typeof ex.time_exit_dte === "number") {
    exitParts.push(ex.time_exit_dte === 0 ? "hold to expiry" : `${ex.time_exit_dte} DTE`);
  }
  if (typeof ex.close_at_time === "string") exitParts.push(`flat ${ex.close_at_time}`);
  if (typeof ex.delta_stop_abs === "number") exitParts.push(`Δ-stop ${ex.delta_stop_abs}`);
  if (!exitParts.length && Array.isArray(ex.conditions) && ex.conditions.length) {
    exitParts.push("on exit signal");
  }

  const cadence = b.schedule?.frequency
    ? b.schedule.frequency +
      (b.schedule.day_of_week ? ` · ${b.schedule.day_of_week.slice(0, 3)}` : "") +
      (b.schedule.time_of_day ? ` · ${b.schedule.time_of_day} ET` : "")
    : null;

  const out: (Box | null)[] = [
    b.ticker ? { label: "TICKER", value: b.ticker } : null,
    b.structure
      ? { label: "STRUCTURE", value: STRUCTURE_LABEL[b.structure as Structure] ?? b.structure }
      : null,
    strike ? { label: "STRIKE", value: strike } : null,
    b.expiration_selection?.target_dte != null
      ? {
          label: "TENOR",
          value: b.expiration_selection.target_dte === 0
            ? "0 DTE"
            : `${b.expiration_selection.target_dte} DTE`,
        }
      : null,
    cadence ? { label: "CADENCE", value: cadence } : null,
    b.sizing?.value != null
      ? {
          label: "SIZE",
          value: b.sizing.method === "fixed_contracts"
            ? `${b.sizing.value} contract${b.sizing.value === 1 ? "" : "s"}`
            : `${b.sizing.value}% risk`,
        }
      : null,
    b.initial_capital != null
      ? { label: "CAPITAL", value: fmtMoney(b.initial_capital) }
      : null,
    {
      label: "CLOCK",
      value: (b.clock ?? "daily") + (b.resolution === "finest" ? " · finest" : ""),
    },
    b.intraday_scan === "every_setup" ? { label: "SCANNING", value: "every setup" } : null,
    { label: "WINDOW", value: windowLabel(b.window?.start, b.window?.end) },
    exitParts.length ? { label: "EXIT", value: exitParts.join(" · ") } : null,
    b.conditions?.length
      ? {
          label: "ENTRY LOGIC",
          value: b.conditions
            .map((c) => `${c.indicator}${c.period ? `(${c.period})` : ""} ${c.operator} ${c.value}`)
            .join(" · "),
        }
      : null,
    b.scale_in?.rungs?.length
      ? {
          label: "LADDER",
          value: `${b.scale_in.rungs.length} rungs · cap ${b.scale_in.max_total_contracts ?? "—"}`,
        }
      : null,
    b.costs
      ? {
          label: "COSTS",
          value: `$${b.costs.commission_per_contract ?? "—"}/ct · slip ${b.costs.slippage_half_spread_fraction ?? "—"}/${b.costs.slippage_half_spread_fraction_sell ?? "—"}`,
        }
      : null,
  ];
  return out.filter((x): x is Box => x !== null);
}

/** V-13 section 5: the field-level diff against the parent, stored at
 * creation from the SAME comparison the lock check and zero-edit guard read
 * (V-162). Old and new per row, in the story's own register; absent on
 * non-variants, so nothing renders there (V-175). */
function WhatChanged({ prov }: { prov: RunProvenance }) {
  const rows = prov.what_changed;
  if (!rows || rows.length === 0) return null;
  return (
    <div className={`${PANEL} px-5 py-4`}>
      <div className={`${LABEL} mb-3`}>WHAT CHANGED — VS THE PARENT RUN</div>
      <div className="flex flex-col gap-2">
        {/* V-208: same FieldName and ValueChange the SUPERSEDED marker uses, so a
            field reads identically in both places on this page */}
        {rows.map((r) => (
          <div key={r.field} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <FieldName field={r.field} label={r.label} />
            <ValueChange parent={r.parent} variant={r.variant} />
          </div>
        ))}
      </div>
    </div>
  );
}

function DecisionGrid({ prov }: { prov: RunProvenance }) {
  const confirmed = prov.confirmed;
  if (!confirmed) return null;
  if (confirmed.omitted) {
    return (
      <div className={`${PANEL} px-5 py-4`}>
        <div className={`${LABEL} mb-1.5`}>THE CONFIRMED SPEC</div>
        <div className="font-mono text-[12px] text-ink-4">{confirmed.omitted}</div>
      </div>
    );
  }
  const boxes = confirmed.draft
    ? boxesFromDraft(confirmed.draft, confirmed.costs)
    : confirmed.boxes
      ? boxesFromSpec(confirmed.boxes)
      : [];
  if (!boxes.length) return null;
  return (
    <div className={`${PANEL} px-5 py-4`}>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <div className={LABEL}>
          {confirmed.derived ? "THE SPEC AS RUN" : "THE CONFIRMED SPEC"}
        </div>
        {confirmed.derived && (
          <span className="font-mono text-[10.5px] text-ink-4">
            derived from the stored spec — the confirmed draft predates recording
          </span>
        )}
        {confirmed.untouched === true && (
          <span className="font-mono text-[10.5px] text-ink-4">
            parser spec ran verbatim
          </span>
        )}
        {confirmed.untouched === false && (
          <span className="font-mono text-[10.5px] text-ink-4">
            dials edited after parsing
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-3.5 sm:grid-cols-3 lg:grid-cols-4">
        {boxes.map((box) => (
          <div key={box.label}>
            <div className={`${LABEL} mb-0.5`}>{box.label}</div>
            <div className="font-mono text-[13px] leading-snug text-ink">{box.value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------- mechanics
function MechanicsLine({ prov }: { prov: RunProvenance }) {
  const m = prov.mechanics;
  if (!m) {
    return (
      <div className={`${PANEL} px-5 py-4`}>
        <div className={`${LABEL} mb-1.5`}>RUN MECHANICS</div>
        <div className="font-mono text-[12px] text-ink-4">
          mechanics not recorded — the run predates measurement or did not complete
        </div>
      </div>
    );
  }
  const total = (m.engine_s ?? 0) + (m.gauntlet_s ?? 0) + (m.verdict_s ?? 0);
  const parts: string[] = [];
  // any recorded duration gets the headline — a measured 0s is "<1s", not
  // "no total" (the falsy-zero trap)
  if (m.engine_s != null || m.gauntlet_s != null || m.verdict_s != null) {
    parts.push(`ran in ${duration(total)}`);
  }
  if (m.engine_s != null && m.gauntlet_s != null) {
    parts.push(`engine ${duration(m.engine_s)} · gauntlet ${duration(m.gauntlet_s)}`);
  }
  const mix = m.resolution_mix
    ? Object.entries(m.resolution_mix)
        .map(([k, v]) => `${k} ${v}`)
        .join(" / ")
    : null;
  if (m.sessions != null && m.clock) {
    parts.push(
      `${m.sessions.toLocaleString("en-US")} sessions at the ${m.clock} clock` +
        (mix ? ` (${mix})` : ""),
    );
  }
  if (m.effective_start && m.effective_end) {
    parts.push(`over ${shortDate(m.effective_start)} → ${shortDate(m.effective_end)}`);
  }
  if (m.build) {
    const build: string[] = [];
    if (m.build.spec_version != null) build.push(`spec v${m.build.spec_version}`);
    if (m.build.fill_model) build.push(`fill model ${m.build.fill_model}`);
    build.push(`build ${m.build.commit ? m.build.commit.slice(0, 7) : "local"}`);
    parts.push(build.join(" · "));
  }
  return (
    <div className={`${PANEL} px-5 py-4`}>
      <div className={`${LABEL} mb-1.5`}>RUN MECHANICS</div>
      {parts.length ? (
        <div className="font-mono text-[12.5px] leading-[1.7] text-ink-2">
          {parts.join(" · ")}
        </div>
      ) : (
        // a mechanics object whose fields are all unrenderable (partial old
        // perf rows) still gets the honest line, never an empty panel
        <div className="font-mono text-[12px] text-ink-4">
          mechanics not recorded — the run predates measurement or did not complete
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------- view
export function HowBuilt({ run }: { run: RunPayload }) {
  const prov = run.provenance;
  // V-155: the parent named the way the Library names it; the id is only ever
  // a link target. get_run already resolved the label (and whether the parent
  // was deleted), so nothing here re-derives it.
  const carried = prov?.carried_from ?? null;
  const parentLabel = run.variant?.parent.deleted
    ? null
    : (run.variant?.parent.label ?? null);
  if (!prov) {
    // real runs always carry a record (stored or derived) — this is the
    // safety net for demo runs and unreadable rows, so it claims nothing
    return (
      <div className={`${PANEL} mt-2 px-5 py-8 text-center`}>
        <div className="font-mono text-[12.5px] text-ink-4">
          no setup story is available for this run
        </div>
      </div>
    );
  }

  const events = prov.conversation;
  const exchanges = events ? pairConversation(events) : null;
  // V-200: keyed by index into the stored conversation. Absence is STILL HOLDS,
  // so a payload without this object degrades to the state A1 already shipped.
  const supersededByIndex = new Map<number, Superseded>(
    (prov.reconciliation?.labels ?? []).map((label) => [label.answer_index, label]),
  );
  const pins = prov.prompt?.chart?.pins ?? [];

  return (
    <div className="mx-auto mt-2 flex max-w-[820px] flex-col gap-3.5">
      <h2 className="mt-2 font-serif text-[26px] font-medium leading-tight tracking-[-.01em]">
        How this was built
      </h2>

      {/* origin note — STORED records only. A derived record's note repeats
          the disclosure the conversation slot below already owns (the exact
          double-print a review caught), so derived rows render only their
          lineage link, wording-free. */}
      {prov.note && !prov.derived && (
        <div className={`${PANEL} px-5 py-3.5`}>
          <div className="font-mono text-[12.5px] leading-[1.6] text-ink-2">
            {prov.note}
            {prov.parent_run_id && (
              <>
                {" · "}
                <Link
                  href={`/runs/${prov.parent_run_id}`}
                  className="text-trust underline decoration-trust-border underline-offset-2 hover:decoration-trust"
                >
                  view the original run ›
                </Link>
              </>
            )}
          </div>
        </div>
      )}
      {prov.derived && prov.parent_run_id && (
        <div className={`${PANEL} px-5 py-3.5`}>
          <Link
            href={`/runs/${prov.parent_run_id}`}
            className="font-mono text-[12.5px] text-trust underline decoration-trust-border underline-offset-2 hover:decoration-trust"
          >
            view the original run ›
          </Link>
        </div>
      )}

      {prov.prompt?.text && (
        <div className="flex justify-end">
          <div className="max-w-[75%] rounded-[12px_12px_4px_12px] border border-line bg-raised px-3.5 py-2.5 font-mono text-[13px] leading-[1.55] text-ink-2">
            “{prov.prompt.text}”
          </div>
        </div>
      )}

      {prov.source === "chart" && pins.length > 0 && (
        <div className={`${PANEL} px-5 py-4`}>
          <div className={`${LABEL} mb-2`}>
            TAUGHT ON THE {prov.prompt?.chart?.ticker ?? ""} CHART
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-1.5">
            {pins.map((pin, i) => (
              <span key={i} className="font-mono text-[12.5px] text-ink-2">
                {pinLabel(pin.entry)}
                {pin.exit ? ` → ${pinLabel(pin.exit)}` : " → (no exit pinned)"}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* V-31: the header states WHERE these questions were asked, which stays
          load-bearing now that A2 has landed. A2's markers answer a different
          question — which answers this run's edits superseded — and they are
          silent on the rest by design (V-200 matches on stored values, and
          measured against production only about a third of recorded answers
          can ever match one). So an unmarked exchange means "not shown to be
          superseded", never "verified as still true", and the header carries
          the misattribution work regardless of how many markers appear.
          Stated plainly, never euphemistically: no phrasing may let a skimming
          reader believe the interview ran twice. */}
      {carried && exchanges !== null && exchanges.length > 0 && (
        <div className="rounded-[14px] border border-trust-border bg-trust-dim px-5 py-3.5">
          <div className="font-mono text-[12.5px] leading-[1.6] text-ink-2">
            These questions were asked on{" "}
            {parentLabel ? (
              <a
                href={`/runs/${carried}`}
                className="text-trust underline decoration-trust-border underline-offset-2 hover:decoration-trust"
              >
                {parentLabel}
              </a>
            ) : (
              "the original run"
            )}
            , not on this run.{" "}
            <a
              href={`/runs/${carried}`}
              className="text-trust underline decoration-trust-border underline-offset-2 hover:decoration-trust"
            >
              See how that run was built ›
            </a>
          </div>
        </div>
      )}

      {exchanges === null ? (
        <div className="rounded-[14px] border border-dashed border-line px-5 py-4 text-center">
          <span className="font-mono text-[12px] text-ink-4">
            {/* automatic origin outranks derived: an old auto run has no
                conversation BECAUSE it was automatic, not because of when
                it ran */}
            {prov.origin && prov.origin !== "user"
              ? "no conversation — this run was started automatically"
              : carried
                ? // V-176: no compile happened here, so it cannot be
                  // "not captured" for THIS run either
                  "no conversation carried — the original run recorded none"
                : prov.derived
                  ? "conversation not captured (predates provenance recording)"
                  : "no conversation was captured for this run"}
          </span>
        </div>
      ) : exchanges.length === 0 ? (
        <div className="rounded-[14px] border border-dashed border-line px-5 py-4 text-center">
          <span className="font-mono text-[12px] text-ink-4">
            {/* V-176: "the strategy compiled directly" describes an event that
                did not happen on a variant — no compile ran here at all. The
                truth, in the carried register. */}
            {carried
              ? "the original run needed no clarifying questions, and this run asked none — it is a variant"
              : "no clarifying questions were needed — the strategy compiled directly"}
          </span>
        </div>
      ) : (
        exchanges.map((x, i) => (
          <ExchangeCard
            key={i}
            x={x}
            carried={Boolean(carried)}
            superseded={
              x.answerIndex === undefined ? undefined : supersededByIndex.get(x.answerIndex)
            }
          />
        ))
      )}

      {prov.truncated && (
        <div className="text-center font-mono text-[11px] text-ink-4">
          … {prov.truncated.dropped_events} later exchange
          {prov.truncated.dropped_events === 1 ? "" : "s"} dropped by the
          record’s size cap
        </div>
      )}

      <DecisionGrid prov={prov} />
      <WhatChanged prov={prov} />
      <MechanicsLine prov={prov} />
    </div>
  );
}
