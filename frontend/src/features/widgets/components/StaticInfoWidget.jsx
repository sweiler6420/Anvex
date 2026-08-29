import WidgetFrame from './WidgetFrame'

/**
 * A block of text (ANV-34). Ported from
 * `AverageInvestorWeb/src/components/shared/widgets/StaticInfoWidget.jsx` (14 lines).
 *
 * The smallest possible widget, and the one that proves the content box scrolls: it has no
 * state, no network and nothing to measure, so anything that goes wrong with it went wrong
 * in the window rather than in the widget.
 *
 * ## Changed from the original
 *
 *  - **The copy is a prop with a default.** The original hardcoded "Resize the window to see
 *    it scale", which is a sentence about the demo it was written for; a widget that can
 *    only ever say one thing is a component with a constant inside it.
 *  - Colours are theme-aware and the duplicated heading is gone — see `WidgetFrame`.
 */

const DEFAULT_TEXT =
  'A widget is an ordinary React node held on the window object. Resize or collapse the ' +
  'window and the content box scrolls; nothing here measures anything.'

export default function StaticInfoWidget({ label = 'Info', text = DEFAULT_TEXT }) {
  return (
    <WidgetFrame label={label} testId="static-info-widget">
      <p className="min-w-0">{text}</p>
    </WidgetFrame>
  )
}
