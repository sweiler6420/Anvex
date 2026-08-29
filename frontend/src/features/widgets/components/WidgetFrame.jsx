/**
 * The shell every widget in this feature renders into (ANV-34).
 *
 * Six consumers inside one feature, so it lives in `features/widgets/components/` rather
 * than `components/ui/` — CLAUDE.md §5 promotes on the second *feature*, not the second
 * import.
 *
 * Three decisions live here, once, instead of six times:
 *
 *  - **No visible title.** Each of the three original widgets opened with its own heading
 *    (`<div className="text-xs …">Counter</div>`), and `DesktopWindow` already renders the
 *    window's title in its header — so on a desktop the name appeared twice, and on a 2×2
 *    window (ANV-33: 40×40 px) the duplicate consumed most of the content box. The name
 *    survives as the section's **accessible** name, which is what a screen reader needed it
 *    for and what a sighted user is already getting from the header.
 *  - **Theme-aware text.** The originals hardcoded `text-neutral-100` / `text-neutral-200`
 *    on a translucent panel — white-on-white in light mode. Colour is set here so no widget
 *    has to remember the pair.
 *  - **`min-h-0` / `min-w-0`.** A flex child's default minimum size is its *content*, so a
 *    long company name or a tall list refuses to shrink and pushes the box wider than the
 *    window instead of scrolling inside it. The window's content box is `overflow: auto`;
 *    this is what lets it do its job.
 *
 * `text-sm` and `text-base` are the only two sizes used anywhere in this feature, because
 * they are the only small ones this Tailwind config defines. Its `fontSize` scale is
 * `sm / base / xl / 2xl / 3xl / 4xl / 5xl` — there is **no `xs`, `md` or `lg`** — so the
 * originals' `text-xs` and `text-md` were not Tailwind classes at all and emitted nothing.
 */

/**
 * @param {object} props
 * @param {string} props.label the widget's accessible name
 * @param {string} [props.className] extra classes on the section
 * @param {string} [props.testId]
 * @param {React.ReactNode} props.children
 */
export default function WidgetFrame({ label, className = '', testId, children }) {
  return (
    <section
      aria-label={label}
      data-testid={testId}
      className={`flex h-full w-full min-h-0 min-w-0 flex-col gap-2 text-sm text-neutral-900 dark:text-neutral-100 ${className}`}
    >
      {children}
    </section>
  )
}
