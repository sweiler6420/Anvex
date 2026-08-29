import { useCallback, useEffect, useId, useState } from 'react'

import { toApiError } from '@lib/api'

import { fetchWatchlist, fetchWatchlists, moveWatchlistStock } from '../api'
import WidgetFrame from './WidgetFrame'

/**
 * A watchlist, in order, reorderable (ANV-34). Ported from
 * `AverageInvestorWeb/src/components/shared/widgets/Watchlist.jsx` (100 lines) — a component
 * exported under the name `Chart`.
 *
 * **This is the frontend half of ANV-15's fix**, and it is worth being precise about which
 * half. The backend moved the reorder from `PUT /v1/watchlist/stock?stock_id=…&
 * destination_index=…&current_index=…` to `PATCH /v1/watchlists/{id}/stocks/{stock_id}` with
 * `{position}` in the body, deleting `current_index` on the way: it was the client's belief
 * about where a row already was, it was stale by construction, and the server could not
 * verify it, so when it was wrong a different stock moved than the one the user had dropped.
 * The client half is that **nothing here computes an index into its own copy of the list**.
 * A move names a stock and a destination, the response is the whole watchlist in its new
 * order, and that response is what gets rendered.
 *
 * The old widget did not persist a reorder **at all**: `handleOnDragEnd` spliced a local
 * array and called `setWatchlist`, and no request was ever sent. The order reverted on
 * reload.
 *
 * ## Why there is no drag-and-drop, and what that means for keyboard users
 *
 * The original used `@hello-pangea/dnd`. It is not brought back, and the decision turns on
 * three things rather than on bundle size alone:
 *
 *  1. **The API is button-shaped, not drag-shaped.** `PATCH …/{stock_id}` with `{position}`
 *     is "this stock, to this index" — which is exactly what a *Move up* button is. Drag is
 *     a way of *expressing* a destination that requires a box model to interpret; a button
 *     expresses the same destination with no geometry at all.
 *  2. **Keyboard access is the primary affordance here, not a fallback.** ANV-33 flagged the
 *     window palette as mouse-only because an HTML5 drag has no keyboard equivalent. That is
 *     a real accessibility defect and it should not be reproduced in a second place.
 *     `@hello-pangea/dnd` does ship a keyboard mode (space to lift, arrows to move), which is
 *     its main argument — but a pair of ordinary `<button>`s is keyboard-operable, screen
 *     -reader-operable and touch-operable *by construction*, with no lift-and-drop mental
 *     model to teach and nothing to announce that the browser does not announce already.
 *  3. **It could not be tested here.** `@hello-pangea/dnd` measures its droppable to decide
 *     where a drop lands, and jsdom reports 0×0 for everything (ANV-33). The one test this
 *     ticket must not get wrong — *the reorder calls the API with the right arguments* —
 *     would have had to be written against a fabricated layout, i.e. against the fabrication.
 *
 * The cost is stated rather than discovered: dragging a row is a gesture some users expect
 * and it is not available. Adding it later is additive — an HTML5 drag handler that computes
 * a destination index and calls the same `moveWatchlistStock` — and it must stay additive,
 * because the buttons are what make the feature reachable without a pointer.
 *
 * ## Other things the original did that are not reproduced
 *
 *  - **Every row's price was the literal string `$320`.** There was no price in the payload
 *    and there is none now: `WatchlistEntryDetailOut.stock` is a `StockOut`
 *    (`stock_id, ticker_symbol, company, market, isin`) and carries no quote. A hardcoded
 *    number in the position where a price goes is worse than no number, so the row shows the
 *    security's market instead. A real quote needs an endpoint that does not exist.
 *  - The fetch had no cleanup, so a resolved response set state on an unmounted component;
 *    here the request is aborted and `request_cancelled` is swallowed (ANV-24/25).
 *  - `GET /v1/watchlist` returned a bare array. Lists are `Page[T]` now, and a single
 *    watchlist is a `WatchlistDetailOut` whose `entries` are already in `position` order —
 *    so nothing here sorts anything.
 */

/** @param {{watchlistId?: string, title?: string}} props */
export default function WatchlistWidget({ watchlistId, title = 'Watchlist' }) {
  const [state, setState] = useState({ status: 'loading', watchlist: null, error: null })
  const [movingStockId, setMovingStockId] = useState(null)
  const [moveError, setMoveError] = useState(null)
  const [announcement, setAnnouncement] = useState('')

  const statusId = useId()

  useEffect(() => {
    const controller = new AbortController()
    setState({ status: 'loading', watchlist: null, error: null })

    const load = async () => {
      let id = watchlistId
      if (!id) {
        const { items } = await fetchWatchlists({ limit: 1, signal: controller.signal })
        if (items.length === 0) {
          setState({ status: 'none', watchlist: null, error: null })
          return
        }
        id = items[0].watchlist_id
      }
      const watchlist = await fetchWatchlist({ watchlistId: id, signal: controller.signal })
      setState({ status: 'ready', watchlist, error: null })
    }

    load().catch((err) => {
      const apiError = toApiError(err)
      if (apiError.code === 'request_cancelled') return
      setState({ status: 'error', watchlist: null, error: apiError })
    })

    return () => controller.abort()
  }, [watchlistId])

  const move = useCallback(
    async (entry, position) => {
      // A move is idempotent while it is in flight: this guard *and* the `disabled`
      // attribute, because a keyboard Enter and a double click fail differently (ANV-29).
      //
      // **This line is an equivalent mutant under jsdom and no test kills it.** Deleting it
      // leaves the whole suite green, because `disabled` is applied on the re-render that
      // `setMovingStockId` triggers and Testing Library's `user.click` awaits that render
      // before the next event — so a second press can never reach a handler here. In a
      // browser it can: a held Enter repeats faster than a commit. The line stays, and this
      // comment is the record ANV-33 asks for rather than a deletion of the wrong half of a
      // redundant pair.
      if (movingStockId) return
      setMovingStockId(entry.stock_id)
      setMoveError(null)
      try {
        const watchlist = await moveWatchlistStock({
          watchlistId: entry.watchlist_id,
          stockId: entry.stock_id,
          position,
        })
        // The server's answer, not a local splice. This is the whole point of the endpoint
        // returning the reordered list.
        setState({ status: 'ready', watchlist, error: null })
        setAnnouncement(
          `${entry.stock.ticker_symbol} moved to position ${position + 1} of ` +
            `${watchlist.entries.length}.`,
        )
      } catch (err) {
        // No `request_cancelled` branch here, deliberately: the move is given no
        // `AbortSignal`, so there is nothing that can cancel it and a branch for it would be
        // code nothing reaches. A move is a write the user asked for — abandoning it on
        // unmount would leave the server and the screen disagreeing — so it is allowed to
        // finish even if the window closes.
        setMoveError(toApiError(err))
      } finally {
        setMovingStockId(null)
      }
    },
    [movingStockId],
  )

  const entries = state.watchlist?.entries ?? []
  const busy = movingStockId !== null

  return (
    <WidgetFrame label={state.watchlist?.title ?? title} testId="watchlist-widget">
      {/* Both live regions are rendered unconditionally and left empty (ANV-29): a region
          has to be in the accessibility tree before its text arrives. */}
      <p
        id={statusId}
        role="status"
        className="sr-only"
        data-testid="watchlist-announcement"
      >
        {announcement}
      </p>
      <p role="alert" className="text-neutral-900 dark:text-neutral-100" data-testid="watchlist-error">
        {state.status === 'error' ? state.error.message : null}
        {moveError ? moveError.message : null}
      </p>

      {state.status === 'loading' ? (
        <p className="text-neutral-500 dark:text-neutral-400">Loading watchlist…</p>
      ) : null}

      {state.status === 'none' ? (
        <p className="text-neutral-500 dark:text-neutral-400">You have no watchlists yet.</p>
      ) : null}

      {state.status === 'ready' && entries.length === 0 ? (
        <p className="text-neutral-500 dark:text-neutral-400">This watchlist is empty.</p>
      ) : null}

      {entries.length > 0 ? (
        <ol className="m-0 min-h-0 min-w-0 flex-1 list-none overflow-auto p-0">
          {entries.map((entry, index) => (
            <li
              key={entry.stock_id}
              data-testid={`watchlist-row-${entry.stock.ticker_symbol}`}
              className="flex min-w-0 items-center gap-2 border-t border-neutral-300 py-2 first:border-t-0 dark:border-neutral-600"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate font-demi">{entry.stock.ticker_symbol}</p>
                <p className="truncate text-neutral-600 dark:text-neutral-300">
                  {entry.stock.company}
                </p>
              </div>
              <span className="shrink-0 text-neutral-500 dark:text-neutral-400">
                {entry.stock.market}
              </span>
              <span className="flex shrink-0 gap-1">
                <MoveButton
                  label={`Move ${entry.stock.ticker_symbol} up`}
                  glyph="↑"
                  disabled={busy || index === 0}
                  onClick={() => move(entry, index - 1)}
                />
                <MoveButton
                  label={`Move ${entry.stock.ticker_symbol} down`}
                  glyph="↓"
                  disabled={busy || index === entries.length - 1}
                  onClick={() => move(entry, index + 1)}
                />
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </WidgetFrame>
  )
}

const MOVE_BUTTON_CLASS =
  'rounded bg-neutral-200 px-2 py-1 text-neutral-900 disabled:opacity-40 ' +
  'hover:bg-neutral-300 focus-visible:outline focus-visible:outline-2 ' +
  'focus-visible:outline-offset-2 focus-visible:outline-brand-500 ' +
  'dark:bg-neutral-700 dark:text-white dark:hover:bg-neutral-600'

/**
 * One reorder control.
 *
 * The glyph is `aria-hidden` and the name comes from `aria-label`, so the button announces
 * "Move NVDA up, button" rather than "up arrow". `type="button"` because a widget can be
 * dropped anywhere, including inside a form, and an untyped button submits it.
 *
 * `disabled` rather than `aria-disabled` here, deliberately: ANV-32's rule reserves
 * `aria-disabled` for a control switched off **for a reason worth reading**, and "this row
 * is already first" is a reason the `<ol>` has already conveyed.
 */
function MoveButton({ label, glyph, disabled, onClick }) {
  return (
    <button
      type="button"
      className={MOVE_BUTTON_CLASS}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
    >
      <span aria-hidden="true">{glyph}</span>
    </button>
  )
}
