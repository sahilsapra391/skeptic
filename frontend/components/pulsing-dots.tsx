/** The app's "working" glyph: three trust-hue dots pulsing in a wave.
 * One component so the stagger timing and styling can't drift between the
 * thinking indicator, the verdict narration line, and future uses. */

export function PulsingDots({ size = 5 }: { size?: number }) {
  const px = `${size}px`;
  return (
    <span className="flex items-center gap-[3px]" aria-hidden>
      {[0, 0.35, 0.7].map((delay) => (
        <span
          key={delay}
          className="animate-pin-pulse rounded-full bg-trust"
          style={{ width: px, height: px, animationDelay: `${delay}s` }}
        />
      ))}
    </span>
  );
}
