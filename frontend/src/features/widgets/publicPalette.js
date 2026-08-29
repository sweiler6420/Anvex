import { WIDGET_PALETTE } from './palette'

/**
 * The palette a **logged-out** visitor may be offered (ANV-35).
 *
 * Two of the five widgets fetch through `authApi` when they mount, so on the marketing page
 * they would 401 and render an error state to somebody who has no way to fix it. This is the
 * subset that does not — and it is **derived from a flag on each row**, never listed, because
 * a hand-written list of three names is a literal that drifts the moment a sixth widget is
 * added by somebody who never opens this file. See `palette.jsx` for the flag and the full
 * argument.
 *
 * `item.network === false`, not `!item.network`: a row that never declares the field is
 * `undefined`, and the strict comparison keeps it out. The filter is an **opt-in**, so
 * forgetting to think about it lands on the safe side — and `palette.test.jsx` fails the
 * suite for the missing flag as well, so it does not land there silently.
 *
 * **`!item.network` is an equivalent mutant today and is still the wrong spelling.** With
 * every row declaring a boolean the two predicates agree, and no test separates them — they
 * differ only for a row that omits the flag, which the suite refuses on the *next* line
 * anyway. What `=== false` buys is the window between writing that row and running the
 * tests: an opt-in that reads as an opt-in cannot be misread by the person adding the sixth
 * widget. Recorded here rather than deleted (ANV-33's rule).
 *
 * ## Why this is its own module
 *
 * `palette.jsx` holds JSX and must stay a `.jsx` file; `react-refresh/only-export-components`
 * then treats a second capitalised export beside `WIDGET_PALETTE` as a non-component export
 * in a component module and warns. That is CLAUDE.md §5's split-the-file rule arriving from a
 * slightly different direction (ANV-25 and ANV-27 split a provider for it), and the split is
 * free here: a filter over an existing array needs no JSX.
 */
export const PUBLIC_WIDGET_PALETTE = WIDGET_PALETTE.filter((item) => item.network === false)
