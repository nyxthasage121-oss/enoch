/**
 * effects.js — Visual enhancement components for Enoch
 * Registers Alpine data components: cardSpotlight
 * Must load (deferred) before alpine.min.js so alpine:init fires after.
 */

document.addEventListener('alpine:init', () => {

  // ── Card Spotlight + Tilt ───────────────────────────────────────
  // Usage in template:
  //   <a x-data="cardSpotlight" @mousemove="onMove" @mouseleave="onLeave"
  //      :style="tiltStyle" class="relative overflow-hidden …">
  //     <div class="card-glare" :style="glareStyle"></div>
  //     … card content …
  //   </a>

  Alpine.data('cardSpotlight', () => ({
    active: false,
    gx: 50,    // glare centre x  (0–100)
    gy: 50,    // glare centre y  (0–100)
    rx: 0,     // tilt rotateX deg
    ry: 0,     // tilt rotateY deg

    onMove(e) {
      const r  = this.$el.getBoundingClientRect();
      const nx = (e.clientX - r.left) / r.width;
      const ny = (e.clientY - r.top)  / r.height;
      this.gx     = nx * 100;
      this.gy     = ny * 100;
      this.rx     = -(ny - 0.5) * 7;   // ±3.5°
      this.ry     =  (nx - 0.5) * 7;
      this.active = true;
    },

    onLeave() {
      this.active = false;
      this.rx = 0;
      this.ry = 0;
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

});
