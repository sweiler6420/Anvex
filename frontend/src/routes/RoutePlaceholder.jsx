/**
 * The smallest honest stand-in for a page that does not exist yet (ANV-27).
 *
 * ANV-27 owns the route tree and the guards; the screens are ANV-29..36. Rather than
 * sketch seven half-pages that would each have to be unpicked, every route renders this
 * one component with its own title. It exists to make two things assertable — *which*
 * route resolved, and that it resolved at all — and to be deleted a line at a time as each
 * page lands.
 *
 * **Replacing one is a one-line edit in the route module**, swapping this element for the
 * feature component. CLAUDE.md §5's "routes are thin" means nothing else in the route file
 * changes.
 *
 * @param {{title: string, ticket: string, children?: React.ReactNode}} props
 */
export default function RoutePlaceholder({ title, ticket, children }) {
  return (
    <section
      className="flex min-h-screen flex-col items-center justify-center gap-3 bg-neutral-50 p-8 text-neutral-900 dark:bg-neutral-950 dark:text-neutral-300"
      data-testid={`route-${title.toLowerCase().replace(/\s+/g, '-')}`}
    >
      <h1 className="font-gothic text-4xl text-brand-600 dark:text-brand-400">{title}</h1>
      <p className="font-base text-sm text-neutral-500">Coming in {ticket}.</p>
      {children}
    </section>
  )
}
