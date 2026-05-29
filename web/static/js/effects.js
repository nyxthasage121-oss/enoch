/**
 * effects.js — Visual enhancement components for Enoch
 * Registers Alpine data components: cardSpotlight, panelGlow
 * Must load (deferred) before alpine.min.js so alpine:init fires after.
 */

document.addEventListener('alpine:init', () => {

  // ── Animated number counter ─────────────────────────────────────
  // Usage:
  //   <span x-data="counter(0, {{ char.xp_total }})" x-text="value"></span>
  //   <span x-data="counter(0, {{ char.xp_available }}, 600)" x-text="value"></span>
  Alpine.data('counter', (initial, target, duration = 800) => ({
    value: initial,
    init() {
      if (target === initial) return;
      const start = performance.now();
      const tick  = (t) => {
        const p     = Math.min(1, (t - start) / duration);
        const eased = 1 - Math.pow(1 - p, 3);
        this.value  = Math.round(initial + (target - initial) * eased);
        if (p < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    },
  }));

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
           + `rgba(212,169,77,0.22) 0%,rgba(176,138,62,0.08) 38%,transparent 65%);`
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

// ── Ember particles ─────────────────────────────────────────────
// Slow-drifting embers in the background. Pure canvas, no deps.
// Sits behind content (z-index < gilded panels) so nothing is occluded.
// Respects prefers-reduced-motion.
document.addEventListener('DOMContentLoaded', () => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (document.querySelector('canvas[data-embers]')) return;

  const canvas = document.createElement('canvas');
  canvas.dataset.embers = '1';
  canvas.style.cssText  = 'position:fixed;inset:0;pointer-events:none;z-index:0;opacity:0.5;';
  const sync = () => {
    canvas.width  = window.innerWidth;
    canvas.height = window.innerHeight;
  };
  sync();
  document.body.appendChild(canvas);
  window.addEventListener('resize', sync);

  const ctx       = canvas.getContext('2d');
  const particles = [];
  const MAX       = 22;

  function spawn() {
    particles.push({
      x:         Math.random() * canvas.width,
      y:         canvas.height + 8,
      vx:        (Math.random() - 0.5) * 0.18,
      vy:        -(0.20 + Math.random() * 0.40),
      size:      0.8 + Math.random() * 1.6,
      life:      0,
      maxLife:   6000 + Math.random() * 5000,
      // Mostly gold embers, a few blood-red ones for variety
      hue:       Math.random() < 0.75 ? '200,138,62' : '139,26,26',
      drift:     (Math.random() - 0.5) * 0.0008,   // sideways sway
    });
  }

  let last = performance.now();
  function frame(now) {
    const dt = Math.min(50, now - last);  // clamp dt so tab-resume doesn't burst-spawn
    last     = now;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Maintain a slow spawn rate
    if (particles.length < MAX && Math.random() < dt * 0.0012) spawn();

    for (let i = particles.length - 1; i >= 0; i--) {
      const p     = particles[i];
      p.life     += dt;
      p.vx       += p.drift * dt;
      p.x        += p.vx * dt * 0.08;
      p.y        += p.vy * dt * 0.08;
      const t     = p.life / p.maxLife;
      // Fade in over 20%, fade out over last 50%
      let alpha   = 1;
      if (t < 0.2)      alpha = t / 0.2;
      else if (t > 0.5) alpha = (1 - t) / 0.5;
      alpha       = Math.max(0, alpha) * 0.55;

      ctx.beginPath();
      ctx.fillStyle = `rgba(${p.hue},${alpha})`;
      ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      ctx.fill();
      // Soft glow
      ctx.beginPath();
      ctx.fillStyle = `rgba(${p.hue},${alpha * 0.18})`;
      ctx.arc(p.x, p.y, p.size * 3, 0, Math.PI * 2);
      ctx.fill();

      if (p.life >= p.maxLife || p.y < -10) particles.splice(i, 1);
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
});
