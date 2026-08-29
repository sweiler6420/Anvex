import { StockChartWidget, WIDGET_PALETTE } from '@features/widgets'

/**
 * Window templates built for a *particular* security (ANV-36).
 *
 * `WIDGET_PALETTE` is a fixed table: its chart row carries `<StockChartWidget />` with no
 * props, which is the widget's `ticker = 'AAPL'` default. That is right for a palette — a
 * chip cannot ask which security — and wrong for `/research`, where the user has just
 * picked one from the securities list. So the palette's row is the *base* and this is the
 * one place that substitutes the subject.
 *
 * ## Why this lives in `features/workspace/`
 *
 * ANV-35's rule: where two features meet, a third folder. `features/research/` is a page,
 * and a page importing a widget component to build a window object would make it a second
 * composition point — the exact thing `features/workspace/` exists to be the only one of.
 * So the page hands `openWindow` an item and never imports `@features/widgets` beyond the
 * palette constant it is told to pass in.
 *
 * ## The base row is found by component identity, not by name
 *
 * `'Chart'` is a label on a chip. Somebody renaming it to `'Price'` is making a copy
 * change and has no reason to look in this file, and a `find` on the string would then
 * quietly return `undefined` — a template that produces `null` from `addFromTemplate`,
 * which the page reports as "no room". The component *is* the thing being looked for, so
 * that is what is matched on, and a missing row is a `TypeError` at import rather than a
 * button that lies about why it did nothing.
 */
const CHART_ROW = WIDGET_PALETTE.find((item) => item.window.content?.type === StockChartWidget)

/** @see CHART_ROW — a deliberate throw at import if the palette ever loses its chart row. */
const CHART_TEMPLATE = CHART_ROW.window

/**
 * A price-chart window for one security, in the shape `InteractiveDesktop.openWindow` takes.
 *
 * The `stockId` is what the request is keyed on (`GET /v1/stocks/{stock_id}/data`) and the
 * `ticker` is what the chart is *labelled* with — `StockChartWidget` takes both for exactly
 * this reason, so the accessible name says `NVDA` while the URL names the row. Passing the
 * ticker alone would work and would resolve it a second time on the server.
 *
 * @param {{stockId: string, ticker: string}} security
 * @returns {{name: string, window: object}} an item for `openWindow`
 */
export function stockChartWindow({ stockId, ticker }) {
  return {
    name: `${ticker} price chart`,
    window: {
      ...CHART_TEMPLATE,
      title: `${ticker} price`,
      content: <StockChartWidget stockId={stockId} ticker={ticker} />,
    },
  }
}
