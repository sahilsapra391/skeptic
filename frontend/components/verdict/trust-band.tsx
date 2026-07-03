/**
 * The trust band — the signature element's scale, rendered at two sizes
 * (hero inside the Verdict Block, card in library rows). It deliberately
 * reads as a confidence interval, not a score: a band of plausible trust
 * with a marker, never a number or a grade. Refusal renders as a dashed
 * full-width "withheld" band.
 *
 * Trust hue only — P/L tokens are forbidden in this file.
 */

const LABELS = ["noise", "weak", "suggestive", "robust", "proven"];

interface BandProps {
  band?: { left: string; width: string };
  marker?: string;
  withheld?: boolean;
}

export function TrustBandHero({ band, marker }: BandProps) {
  return (
    <div>
      <div className="relative mx-1 mb-0.5 mt-4 h-[46px]">
        <div className="absolute left-0 right-0 top-[15px] h-[2px] bg-band-track" />
        {band && (
          <div
            className="absolute top-[7px] h-[18px] rounded-[5px] border border-trust-border bg-trust-dim"
            style={{ left: `min(${band.left}, calc(100% - ${band.width}))`, width: band.width }}
          />
        )}
        {marker && (
          <div
            className="absolute top-[3px] h-[26px] w-[4px] rounded-[2px] bg-trust"
            style={{ left: `min(${marker}, calc(100% - 4px))` }}
          />
        )}
        <div className="absolute bottom-0 left-0 right-0 flex justify-between font-mono text-[10px] text-ink-4">
          {LABELS.map((l) => (
            <span key={l}>{l}</span>
          ))}
        </div>
      </div>
    </div>
  );
}

export function TrustBandCard({ band, marker, withheld }: BandProps) {
  return (
    <div className="relative mb-2.5 h-[14px]">
      <div className="absolute left-0 right-0 top-[6px] h-[2px] bg-band-track" />
      {withheld ? (
        <div className="absolute left-[4%] top-[2px] h-[10px] w-[92%] rounded-[4px] border border-dashed border-line-hover" />
      ) : (
        <>
          {band && (
            <div
              className="absolute top-[2px] h-[10px] rounded-[4px] border border-trust-border bg-trust-dim"
              style={{ left: `min(${band.left}, calc(100% - ${band.width}))`, width: band.width }}
            />
          )}
          {marker && (
            <div
              className="absolute top-0 h-[14px] w-[3px] rounded-[2px] bg-trust"
              style={{ left: `min(${marker}, calc(100% - 4px))` }}
            />
          )}
        </>
      )}
    </div>
  );
}
