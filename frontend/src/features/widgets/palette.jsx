import CounterWidget from './components/CounterWidget'
import StaticInfoWidget from './components/StaticInfoWidget'
import StockChartWidget from './components/StockChartWidget'
import TextInputWidget from './components/TextInputWidget'
import WatchlistWidget from './components/WatchlistWidget'

/**
 * The widgets, as `WindowMenu` items (ANV-34) — what ANV-35 drags onto the desktop.
 *
 * ANV-33's seam, used as documented: **a window's content is a React node held on the window
 * object**, and `features/desktop/` neither imports a widget nor knows one exists. So this
 * table is the entire integration; adding a widget is a row here and no change at all to the
 * desktop.
 *
 * Holding *elements* rather than component references is deliberate and is what the seam
 * asks for. A React element is an immutable descriptor, not an instance, so two windows made
 * from the same row still mount two independent components — the shared `<CounterWidget />`
 * below does not give them a shared count.
 *
 * ## Every minimum is 2×2, and that is a claim being made
 *
 * ANV-33 fixed 2×2 cells — 40×40 px at the default `cellSize` — as the size a widget must
 * survive, and `minWidth`/`minHeight` are what the window's *collapse* control shrinks to.
 * Advertising anything larger would be hiding the constraint rather than meeting it, so every
 * row says 2 and `widgets.smallest.test.jsx` renders each widget in a 40×40 box to keep that
 * honest. The `width`/`height` beside them are the comfortable *initial* sizes; a widget is
 * expected to look better at those and to remain usable at the minimum.
 *
 * Colours are the window chrome's, not the widget's — they tint `DesktopWindow`'s header and
 * the palette chip, and nothing inside a widget reads them.
 */
export const WIDGET_PALETTE = [
  {
    name: 'Counter',
    color: '#3b82f6',
    window: {
      title: 'Counter',
      color: '#3b82f6',
      width: 8,
      height: 5,
      minWidth: 2,
      minHeight: 2,
      content: <CounterWidget />,
    },
  },
  {
    name: 'Info',
    color: '#8b5cf6',
    window: {
      title: 'Info',
      color: '#8b5cf6',
      width: 10,
      height: 6,
      minWidth: 2,
      minHeight: 2,
      content: <StaticInfoWidget />,
    },
  },
  {
    name: 'Echo',
    color: '#f59e0b',
    window: {
      title: 'Echo input',
      color: '#f59e0b',
      width: 10,
      height: 5,
      minWidth: 2,
      minHeight: 2,
      content: <TextInputWidget />,
    },
  },
  {
    name: 'Chart',
    color: '#06b6d4',
    window: {
      title: 'Price chart',
      color: '#06b6d4',
      width: 18,
      height: 11,
      minWidth: 2,
      minHeight: 2,
      content: <StockChartWidget />,
    },
  },
  {
    name: 'Watchlist',
    color: '#22c55e',
    window: {
      title: 'Watchlist',
      color: '#22c55e',
      width: 14,
      height: 11,
      minWidth: 2,
      minHeight: 2,
      content: <WatchlistWidget />,
    },
  },
]
