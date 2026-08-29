// Tailwind v3 runs as a PostCSS plugin. The legacy repo had no postcss config at all,
// which is the main reason its `tailwindcss ^4` dependency never actually did anything.
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
