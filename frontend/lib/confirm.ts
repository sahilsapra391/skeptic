/**
 * V-36: the moment a draft first exists, the values that will actually run are
 * stamped onto it.
 *
 * Fill costs and the seed used to be applied at SUBMIT, reaching past whatever
 * the user confirmed on the spec screen. Settings are defaults at parse time
 * and inert afterwards, so they are resolved here, once, and `startBacktest`
 * has nothing left to override.
 *
 * Deliberately pure and settings-injected rather than reading `getSettings()`
 * itself: this module has to be importable outside a browser, because the V-18
 * round-trip guard executes it under node against the real source.
 */

import type { SpecDraft } from "./types";

/** Just the cost dials — the caller passes `getSettings()`, which is a superset. */
export interface CostSettings {
  commission: number;
  slippage: number;
  slippageSell: number;
}

export function confirmDefaults(
  draft: SpecDraft,
  parsedSpec: Record<string, unknown> | null | undefined,
  settings: CostSettings,
): SpecDraft {
  const parsedBacktest = (parsedSpec?.backtest ?? {}) as Record<string, unknown>;
  return {
    ...draft,
    // a draft that already carries these keeps its own — the hook a variant
    // needs, so a copy inherits its parent's costs rather than the copier's
    // current Settings
    costs: draft.costs ?? {
      commission_per_contract: settings.commission,
      slippage_half_spread_fraction: settings.slippage,
      slippage_half_spread_fraction_sell: settings.slippageSell,
    },
    seed: draft.seed ?? (parsedBacktest.seed as number | undefined) ?? 42,
  };
}
