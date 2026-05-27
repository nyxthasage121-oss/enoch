/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./static/js/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        // ── Zillah palette ──────────────────────────────────
        ink: {
          950: '#0a0708',
          900: '#120d0f',
          850: '#171012',
          800: '#1f1618',
          700: '#2a1f22',
        },
        bone: {
          DEFAULT: '#ece4d6',
          muted:   '#c9bfb0',
          dim:     '#8b8275',
        },
        blood: {
          DEFAULT: '#8a2424',
          bright:  '#b03030',
          deep:    '#3a1418',
        },
        gold: {
          DEFAULT: '#b08a3e',
          bright:  '#d4a94d',
          dim:     '#7a5e29',
        },
        mauve: {
          DEFAULT: '#4a3d44',
          dim:     '#352b30',
        },
        // ── Clan accent passthrough (set via --clan CSS var) ─
        clan: 'var(--clan)',
      },
      fontFamily: {
        display: ['"Cinzel"', 'serif'],
        script:  ['"Cormorant Garamond"', 'serif'],
        serif:   ['"EB Garamond"', 'serif'],
        sans:    ['Inter', 'system-ui', 'sans-serif'],
      },
      borderRadius: {
        DEFAULT: '2px',
        sm:      '1px',
        md:      '2px',
        lg:      '2px',
        xl:      '2px',
        '2xl':   '2px',
        full:    '9999px',
      },
      fontSize: {
        '2xs': ['0.65rem', { lineHeight: '1rem' }],
      },
    },
  },
  plugins: [],
}
