/**
 * The composed desktop (ANV-35) — the public surface of `features/workspace/`.
 *
 * `features/desktop/` is the window *system* and knows nothing about widgets;
 * `features/widgets/` is the widgets and knows nothing about windows. This feature is the
 * one place the two meet, which is why it is a folder of its own rather than a file in
 * either of them: `/` (ANV-32's `Workflow` panel) and `/research` (ANV-36) both want the
 * same composition with different palettes.
 *
 * **The default palette is the public one.** `InteractiveDesktop` with no props offers only
 * the widgets that make no network call, because the page that takes no props is the
 * marketing page a logged-out visitor sees. `/research` opts into the full palette
 * explicitly with `items={WIDGET_PALETTE}`.
 */

export { default as InteractiveDesktop } from './components/InteractiveDesktop'
