/* <sk-draw> — inlines the brand draw-on wordmark SVG (design system,
   assets/brand/skeptic-draw-white.svg) so its paths animate via pathLength
   dash. Theme-recolored via currentColor. Click to replay. */
(function () {
  const CSS = `
  sk-draw{display:inline-block;line-height:0;cursor:pointer;color:var(--ink,#f2f3f5)}
  sk-draw svg{width:100%;height:auto;display:block}
  sk-draw path.w{fill:none;stroke:currentColor;stroke-width:12;stroke-linecap:butt;
    stroke-dasharray:1;stroke-dashoffset:1;
    animation:sk-dw .85s cubic-bezier(.22,.7,.3,1) forwards}
  @keyframes sk-dw{to{stroke-dashoffset:0}}`;
  if (!document.getElementById('sk-draw-css')) {
    const s = document.createElement('style');
    s.id = 'sk-draw-css'; s.textContent = CSS;
    document.head.appendChild(s);
  }
  class SkDraw extends HTMLElement {
    connectedCallback() {
      if (this._init) return; this._init = true;
      const w = this.getAttribute('width');
      if (w) this.style.width = w + 'px';
      this.title = 'replay draw-on';
      fetch(this.getAttribute('src') || 'assets/brand/skeptic-draw-white-u1.svg')
        .then(r => r.text()).then(t => { this.innerHTML = t; });
      this.addEventListener('click', () => this.replay());
    }
    replay() {
      this.querySelectorAll('path.w').forEach(p => {
        const d = p.style.animationDelay;
        p.style.animation = 'none'; void p.getBoundingClientRect();
        p.style.animation = ''; p.style.animationDelay = d;
      });
    }
  }
  if (!customElements.get('sk-draw')) customElements.define('sk-draw', SkDraw);
})();
