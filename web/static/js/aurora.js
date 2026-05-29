/* aurora.js — Visual effects runtime for Enoch.
 *
 * Exposes a single `Aurora` global namespace with three subsystems:
 *   Aurora.sparkles(host, count?)  — fire a sparkle burst at an element
 *   Aurora.embers(host, opts?)     — start a continuous ember stream
 *   Aurora.hero(canvas)            — boot the WebGL hero shader
 *
 * Everything respects prefers-reduced-motion + mobile breakpoint.
 */
(function () {
  'use strict';

  const REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const MOBILE  = window.matchMedia('(max-width: 1024px)').matches;
  const HAS_WEBGL = (() => {
    try {
      const c = document.createElement('canvas');
      return !!(window.WebGLRenderingContext &&
                (c.getContext('webgl') || c.getContext('experimental-webgl')));
    } catch (_) { return false; }
  })();

  // ── Sparkle burst ────────────────────────────────────────────
  // Spawns N short-lived `.aurora-spark` spans on a host element.
  // Host should have `position: relative` (the .aurora-sparkle-host
  // utility does this). Particles self-clean after their animation.
  function sparkles(host, count) {
    if (REDUCED || !host) return;
    count = count || 14;
    const rect = host.getBoundingClientRect();
    if (rect.width === 0) return;
    for (let i = 0; i < count; i++) {
      const span = document.createElement('span');
      span.className = 'aurora-spark';
      const size = 4 + Math.random() * 8;
      span.style.width = span.style.height = size + 'px';
      span.style.left = (Math.random() * 100) + '%';
      span.style.top  = (Math.random() * 100) + '%';
      span.style.animationDelay = (Math.random() * 0.25) + 's';
      host.appendChild(span);
      setTimeout(() => span.remove(), 1400);
    }
  }

  // ── Ember stream ────────────────────────────────────────────
  // Continuous low-density drift of upward embers. Returns a stop()
  // function so callers can tear down (e.g. on Alpine teardown).
  function embers(host, opts) {
    if (REDUCED || !host) return () => {};
    opts = opts || {};
    const rateMs = opts.rateMs || 700;     // spawn every N ms
    const max    = opts.max    || 12;      // cap live particles
    let live = 0;
    const interval = setInterval(() => {
      if (live >= max) return;
      const span = document.createElement('span');
      span.className = 'aurora-ember';
      const left = Math.random() * 100;
      span.style.left = left + '%';
      span.style.bottom = '-6px';
      span.style.setProperty('--dx', ((Math.random() - 0.5) * 40) + 'px');
      span.style.animationDuration = (3 + Math.random() * 3) + 's';
      host.appendChild(span);
      live++;
      setTimeout(() => { span.remove(); live--; }, 6500);
    }, rateMs);
    return () => clearInterval(interval);
  }

  // ── Hero WebGL shader ────────────────────────────────────────
  // Drifting blood mist + slow color shift. Falls back to a CSS
  // gradient (.aurora-hero-fallback) when WebGL is unavailable or
  // when the viewport is mobile-sized.
  function hero(canvas) {
    if (REDUCED || MOBILE || !HAS_WEBGL || !canvas) {
      // Insert the fallback if it isn't there already.
      const parent = canvas && canvas.parentElement;
      if (parent && !parent.querySelector('.aurora-hero-fallback')) {
        const fb = document.createElement('div');
        fb.className = 'aurora-hero-fallback';
        parent.insertBefore(fb, canvas);
      }
      if (canvas) canvas.style.display = 'none';
      return () => {};
    }

    const gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: false });
    if (!gl) return () => {};

    // Simplex-ish noise driven mist + drifting color cells. Cheap
    // because everything's analytic — no textures.
    const vsrc = `
      attribute vec2 a_pos;
      void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }
    `;
    const fsrc = `
      precision mediump float;
      uniform vec2  u_resolution;
      uniform float u_time;

      // Hash + value noise — small, no precision issues
      float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
      float noise(vec2 p) {
        vec2 i = floor(p);
        vec2 f = fract(p);
        float a = hash(i);
        float b = hash(i + vec2(1.0, 0.0));
        float c = hash(i + vec2(0.0, 1.0));
        float d = hash(i + vec2(1.0, 1.0));
        vec2 u = f * f * (3.0 - 2.0 * f);
        return mix(a, b, u.x) + (c - a) * u.y * (1.0 - u.x) + (d - b) * u.x * u.y;
      }
      float fbm(vec2 p) {
        float v = 0.0; float a = 0.5;
        for (int i = 0; i < 5; i++) {
          v += a * noise(p);
          p *= 2.0; a *= 0.5;
        }
        return v;
      }

      void main() {
        vec2 uv = gl_FragCoord.xy / u_resolution.xy;
        // Aspect-correct so the mist doesn't squish on wide screens
        vec2 p = (gl_FragCoord.xy - 0.5 * u_resolution) / u_resolution.y;

        // Two layers of fbm drifting in opposite directions
        float t = u_time * 0.06;
        float n1 = fbm(p * 2.0 + vec2( t,  t * 0.3));
        float n2 = fbm(p * 3.0 + vec2(-t * 0.7, -t * 0.5));
        float mist = smoothstep(0.35, 0.95, n1 * 0.6 + n2 * 0.5);

        // Cinematic gradient — deep crimson into mauve into near-black
        vec3 cold = vec3(0.05, 0.03, 0.06);                       // near-black
        vec3 mauv = vec3(0.32, 0.16, 0.22);                       // mauve mid
        vec3 hot  = vec3(0.68, 0.18, 0.18);                       // crimson glow
        vec3 col  = mix(cold, mauv, smoothstep(0.0, 0.6, mist));
              col = mix(col, hot, smoothstep(0.55, 0.95, mist) * 0.85);

        // Vignette
        float r = length(p * vec2(1.0, 1.2));
        col *= smoothstep(1.4, 0.3, r);

        // Subtle ember sparkle — high-freq noise modulated by mist
        float sparkle = pow(noise(p * 60.0 + u_time * 0.5), 18.0) * mist;
        col += vec3(1.0, 0.8, 0.5) * sparkle * 0.7;

        gl_FragColor = vec4(col, 1.0);
      }
    `;

    function compile(type, src) {
      const sh = gl.createShader(type);
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
        console.warn('Aurora shader compile failed:', gl.getShaderInfoLog(sh));
        return null;
      }
      return sh;
    }
    const vs = compile(gl.VERTEX_SHADER, vsrc);
    const fs = compile(gl.FRAGMENT_SHADER, fsrc);
    if (!vs || !fs) return () => {};

    const prog = gl.createProgram();
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      console.warn('Aurora shader link failed');
      return () => {};
    }
    gl.useProgram(prog);

    // Full-screen quad
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
      -1, -1,  1, -1, -1, 1,
       1, -1,  1,  1, -1, 1,
    ]), gl.STATIC_DRAW);
    const aPos = gl.getAttribLocation(prog, 'a_pos');
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
    const uRes  = gl.getUniformLocation(prog, 'u_resolution');
    const uTime = gl.getUniformLocation(prog, 'u_time');

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = canvas.clientWidth  * dpr;
      const h = canvas.clientHeight * dpr;
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
        gl.viewport(0, 0, w, h);
      }
    };
    window.addEventListener('resize', resize);
    resize();

    let raf = 0;
    const start = performance.now();
    const tick = (now) => {
      gl.uniform2f(uRes, canvas.width, canvas.height);
      gl.uniform1f(uTime, (now - start) * 0.001);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    // Pause when the tab is hidden — saves a real chunk of battery
    const onVis = () => {
      if (document.hidden) { cancelAnimationFrame(raf); raf = 0; }
      else if (!raf)        { raf = requestAnimationFrame(tick); }
    };
    document.addEventListener('visibilitychange', onVis);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', resize);
      document.removeEventListener('visibilitychange', onVis);
    };
  }

  window.Aurora = { sparkles, embers, hero, REDUCED, MOBILE, HAS_WEBGL };
})();
