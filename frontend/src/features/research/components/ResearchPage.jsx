import { useCallback, useRef } from 'react'

import { WIDGET_PALETTE } from '@features/widgets'
import { InteractiveDesktop, RESEARCH_WINDOWS, stockChartWindow } from '@features/workspace'

import SecuritiesPanel from './SecuritiesPanel'

/**
 * `/research` — the first page behind the auth guard (ANV-36).
 *
 * Ported from `AverageInvestorWeb/src/components/authenticated/Research.jsx` (35 lines) and
 * then given the thing that page was a label for.
 *
 * ---------------------------------------------------------------------------------------
 * ## What was ported, verbatim, and what was added
 *
 * The old page is a heading, a panel with a second heading and a sentence, and two cards
 * reading "Stock Analysis / Analyze stocks and market trends" and "Market Research /
 * Research market conditions and opportunities". **All of that copy is ported as found**,
 * including "This is the Research route. You can add your research tools and features
 * here." — ANV-32's rule: a port that quietly improves the wording is a port nobody can
 * review against the original, and the copy is the page owner's.
 *
 * What is new is the second panel: a securities list and the bin-packing desktop, which are
 * the research tools the ported card headings promise. Nothing ported was deleted to make
 * room, so the diff against the original is readable in both directions, and if Stephen
 * decides the two cards are now redundant beside the thing they describe, removing them is
 * a separate, obvious change rather than one buried in this ticket.
 *
 * Three things were changed rather than transcribed, each of them ANV-32's list:
 *
 *  - **The two cards are a `<ul>`/`<li>`**, not two sibling `<div>`s. A repeated card is a
 *    list; Tailwind's preflight already zeroes the marker and the padding, so it is
 *    layout-neutral.
 *  - **`text-lg` is dropped from the card headings.** This Tailwind config's `fontSize`
 *    scale is `sm / base / xl / 2xl / 3xl / 4xl / 5xl` — there is no `lg` — so the class
 *    was not a class and emitted nothing. Removing a rule that never applied changes no
 *    pixels; leaving it in would suggest a size somebody could rely on.
 *  - **The section carries `data-testid="route-research"`**, the id `RoutePlaceholder`
 *    generated, so ANV-27's and ANV-28's routing tests need no edit (ANV-29's rule).
 *
 * `font-xl` on the `<h1>` **is** a real class here (this config defines a `fontWeight` of
 * `xl: 800`), so it stays.
 *
 * ---------------------------------------------------------------------------------------
 * ## The desktop is opted in to the full palette, explicitly
 *
 * `items={WIDGET_PALETTE}`. `InteractiveDesktop` defaults to `PUBLIC_WIDGET_PALETTE` — the
 * rows that make no network call — because the caller that passes nothing is the marketing
 * page a logged-out visitor sees (ANV-35). Taking that default here would silently offer a
 * signed-in user three toys and none of the two widgets that read their data, with nothing
 * on screen to say a subset had been applied. The explicit prop is the security decision
 * being made in the direction that needs saying out loud.
 *
 * The **opening arrangement** is the other half: `RESEARCH_WINDOWS` is derived from the
 * same `network` flag, the other way round, so the desktop opens on the price chart and the
 * watchlist rather than on a counter.
 *
 * ---------------------------------------------------------------------------------------
 * ## The arrangement is not persisted, and that is a stated cost
 *
 * A reload puts every window back where `RESEARCH_WINDOWS` says. For the marketing demo
 * that is correct — a visitor should meet the same three boxes every time. For a signed-in
 * working surface it is a real cost, and it is left rather than hidden: persisting would
 * mean serialising `{id, x, y, width, height}` per window and re-attaching the `content`
 * elements from the palette on the way back in (a React element cannot be stored), plus a
 * decision about *where* — `localStorage` is per-browser and the API has no endpoint for a
 * user's layout. That is a ticket, not a line, and it should be one before anybody arranges
 * a desktop they care about.
 */
/**
 * @param {object} props
 * @param {Function} [props.useContainerSize] ANV-33's measurement seam, forwarded to the
 *   desktop. jsdom has no layout, so a test that needs a window to exist has to invent a
 *   size — and the rule is that the invention is a **named prop** beside the assertion it
 *   supports, never a `ResizeObserver` mock in a fixture two files away. The route renders
 *   `<ResearchPage />` with no props, so nothing in production ever passes it.
 */
export default function ResearchPage({ useContainerSize }) {
  const desktopRef = useRef(null)

  /**
   * Open a chart for the security the user picked.
   *
   * The whole substitution is `stockChartWindow` — see `features/workspace/
   * windowTemplates.jsx` for why the template is built there rather than here. The result
   * is deliberately ignored: `openWindow` has already announced both outcomes into the
   * desktop's own live region, and a second report from here would be the same news twice.
   */
  const handleOpenSecurity = useCallback((security) => {
    desktopRef.current?.openWindow(
      stockChartWindow({ stockId: security.stock_id, ticker: security.ticker_symbol }),
    )
  }, [])

  return (
    <section className="container mx-auto px-4 py-8" data-testid="route-research">
      <h1 className="mb-6 font-gothic text-4xl font-xl">Research</h1>

      <div className="rounded-xl border border-neutral-200 bg-white p-6 dark:border-neutral-700 dark:bg-neutral-900">
        <h2 className="mb-4 font-gothic text-2xl font-medium text-neutral-900 dark:text-neutral-200">
          Research Dashboard
        </h2>
        <p className="mb-4 text-neutral-700 dark:text-neutral-300">
          This is the Research route. You can add your research tools and features here.
        </p>
        <ul className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
          <li className="rounded-lg bg-neutral-100 p-4 dark:bg-neutral-800">
            <h3 className="mb-2 font-gothic font-medium text-neutral-900 dark:text-neutral-200">
              Stock Analysis
            </h3>
            <p className="text-neutral-600 dark:text-neutral-400">
              Analyze stocks and market trends
            </p>
          </li>
          <li className="rounded-lg bg-neutral-100 p-4 dark:bg-neutral-800">
            <h3 className="mb-2 font-gothic font-medium text-neutral-900 dark:text-neutral-200">
              Market Research
            </h3>
            <p className="text-neutral-600 dark:text-neutral-400">
              Research market conditions and opportunities
            </p>
          </li>
        </ul>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <SecuritiesPanel onOpen={handleOpenSecurity} />

        {/* A fixed height, because the desktop measures its container and a `flex-1` inside
            a page that grows with its content measures nothing to grow into. `min-w-0` is
            what lets the grid column shrink below the desktop's intrinsic width instead of
            forcing the page to scroll sideways. */}
        <div
          className="h-[36rem] min-w-0 rounded-xl border border-neutral-200 bg-white p-2 dark:border-neutral-700 dark:bg-neutral-900"
          data-testid="research-desktop"
        >
          <InteractiveDesktop
            ref={desktopRef}
            items={WIDGET_PALETTE}
            initialWindows={RESEARCH_WINDOWS}
            useContainerSize={useContainerSize}
          />
        </div>
      </div>
    </section>
  )
}
