/** @type {import('tailwindcss').Config} */
//
// Carried over from AverageInvestorWeb/tailwind.config.js (ANV-23). The design tokens are
// deliberately identical — brand=cyan, neutral=slate, the compressed font-size scale, the
// RTFont/Poppins families, class-based dark mode and the neon box shadows — because
// ANV-28..36 port that app's components verbatim and any token change here shows up as
// drift on every screen.
//
// **Tailwind v3, on purpose.** The old repo declared `tailwindcss: ^4.1.15` in
// devDependencies but never made it work: it has no `postcss.config.js`, no
// `@tailwindcss/postcss`, and `src/index.css` used the v3 `@tailwind base/components/
// utilities` directives. So there is no working v4 setup to preserve — only a v3-shaped
// config file. See frontend/README.md and CLAUDE.md §5 for the full reasoning.
import colors from 'tailwindcss/colors'

export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  darkMode: 'class',
  theme: {
    screens: {
      sm: '640px',
      md: '768px',
      lg: '1024px',
      xl: '1280px',
      '2xl': '1536px',
      '3xl': '2000px',
    },
    fontSize: {
      sm: '0.800rem',
      base: '1rem',
      xl: '1.250rem',
      '2xl': '1.563rem',
      '3xl': '1.954rem',
      '4xl': '2.442rem',
      '5xl': '3.053rem',
    },
    fontFamily: {
      base: 'Poppins',
      gothic: ['RTFont', 'Poppins', 'sans-serif'],
    },
    fontWeight: {
      normal: '400',
      medium: '500',
      demi: '600',
      bold: '700',
      xl: '800',
    },

    extend: {
      colors: {
        // Semantic brand aliases with full shade scales.
        brand: colors.cyan,
        neutral: colors.slate,
      },
      boxShadow: {
        DEFAULT: '0 0 5px 0px rgba(0, 0, 0, 0.1)',
        lg: '0 0 2px 2px #fff, 0 0 5px #08f, 0 0 15px #08f, 0 0 30px #08f',
        'neon-primary':
          '0 0 3px 2px #ffffff,0 0 5px var(--primary),0 0 10px var(--primary),0 0 25px var(--primary)',
        'neon-primary-sm':
          '0 0 2px 1px #ffffff,0 0 5px var(--primary),0 0 7px var(--primary),0 0 15px var(--primary)',
      },
    },
  },
  plugins: [],
}
