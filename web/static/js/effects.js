/**
 * effects.js — Visual enhancement components for Enoch
 * Registers Alpine data components: cardSpotlight, panelGlow
 * Must load (deferred) before alpine.min.js so alpine:init fires after.
 */

document.addEventListener('alpine:init', () => {

  // ── Card Spotlight + Tilt ───────────────────────────────────────
  // Usage:
  //   <a x-data="cardSpotlight" @mousemove="onMove" @mouseleave="onLeave"
  //      :style="tiltStyle" class="relative overflow-hidden …">
  //     <div class="card-glare" :style="glareStyle"></div>
  //     … card content …
  //   </a>

  Alpine.data('cardSpotlight', () => ({
    active: false,
    gx: 50,
    gy: 50,
    rx: 0,
    ry: 0,

    onMove(e) {
      const r  = this.$el.getBoundingClientRect();
      const nx = (e.clientX - r.left) / r.width;
      const ny = (e.clientY - r.top)  / r.height;
      this.gx     = nx * 100;
      this.gy     = ny * 100;
      this.rx     = -(ny - 0.5) * 7;
      this.ry     =  (nx - 0.5) * 7;
      this.active = true;
    },

    onLeave() {
      this.active = false;
      this.rx     = 0;
      this.ry     = 0;
    },

    get tiltStyle() {
      const ease = this.active ? '0.06s linear' : '0.4s cubic-bezier(0.16,1,0.3,1)';
      const tf   = this.active
        ? `perspective(900px) rotateX(${this.rx}deg) rotateY(${this.ry}deg) scale3d(1.025,1.025,1.025)`
        : `perspective(900px) rotateX(0deg) rotateY(0deg) scale3d(1,1,1)`;
      return `transform:${tf}; transition:transform ${ease}; will-change:transform;`;
    },

    get glareStyle() {
      return `background:radial-gradient(circle at ${this.gx}% ${this.gy}%,`
           + `rgba(176,138,62,0.13) 0%,rgba(176,138,62,0.04) 38%,transparent 68%);`
           + `opacity:${this.active ? 1 : 0};`;
    },
  }));

  // ── Panel Glow (simpler, no tilt) ──────────────────────────────
  // Usage:
  //   <div x-data="panelGlow" @mousemove="onMove" @mouseleave="onLeave"
  //        class="gilded relative overflow-hidden …">
  //     <div class="card-glare" :style="glareStyle"></div>
  //     … content …
  //   </div>

  Alpine.data('panelGlow', () => ({
    active: false,
    gx: 50,
    gy: 50,

    onMove(e) {
      const r  = this.$el.getBoundingClientRect();
      this.gx     = ((e.clientX - r.left) / r.width)  * 100;
      this.gy     = ((e.clientY - r.top)  / r.height) * 100;
      this.active = true;
    },

    onLeave() { this.active = false; },

    get glareStyle() {
      return `background:radial-gradient(circle at ${this.gx}% ${this.gy}%,`
           + `rgba(176,138,62,0.09) 0%,rgba(176,138,62,0.03) 40%,transparent 65%);`
           + `opacity:${this.active ? 1 : 0}; transition:opacity 0.3s ease;`;
    },
  }));

});

// ── Staggered entrance on page-enter children ─────────────────
document.addEventListener('DOMContentLoaded', () => {
  const container = document.querySelector('.page-enter');
  if (!container) return;

  // Animate direct children with a stagger
  const children = container.querySelectorAll(':scope > *');
  children.forEach((el, i) => {
    el.style.opacity    = '0';
    el.style.transform  = 'translateY(10px)';
    el.style.transition = `opacity 0.35s ease ${i * 50}ms, transform 0.35s cubic-bezier(0.16,1,0.3,1) ${i * 50}ms`;
    // Trigger next frame
    requestAnimationFrame(() => requestAnimationFrame(() => {
      el.style.opacity   = '';
      el.style.transform = '';
    }));
  });
});
