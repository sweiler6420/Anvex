import { Outlet } from '@tanstack/react-router'

import Header from './Header'
import SkipLink, { MAIN_CONTENT_ID } from './SkipLink'

/**
 * The application shell (ANV-28), ported from `AverageInvestorWeb/src/components/Layout.jsx`.
 *
 * It is the root route's component, so **every** route renders inside it — the public ones
 * included. That matches the old app's `<Route path="/" element={<Layout/>}>`, and it is
 * why ANV-27 left `rootRoute.component` as a bare `<Outlet />` with a note pointing here:
 * the header is not a feature of the signed-in area, it is the thing a visitor uses to sign
 * in.
 *
 * The old shell was `<main className="App" style={{width:'100%',height:'100%'}}>` wrapping
 * the header *and* the outlet — one `<main>` containing the site navigation, which is
 * exactly what `<main>` is defined not to be (it is the document's primary content, and a
 * screen reader's "skip to main" lands the user back on the nav). Split here into the
 * `<nav>` `Header` renders and a sibling `<main>` holding the outlet, with the inline
 * width/height replaced by `min-h-screen` + `flex-1` so a short page still fills the
 * viewport and the home page's footer has somewhere to sit.
 *
 * **ANV-32 added the skip link** that ANV-28 deferred, and it is a `<button>` rather than
 * an `<a href="#main-content">` because a fragment is part of the location and the location
 * is what `Header` reads to decide which nav item is current. The full argument is in
 * `SkipLink.jsx`; the two lines it needs here are the element itself, rendered **before**
 * `Header` so Tab reaches it first, and `tabIndex={-1}` on the `<main>`, which makes the
 * target programmatically focusable without putting it in the tab order. `focus:outline-none`
 * suppresses a ring on a container the user never chose to focus.
 */
export default function Layout() {
  return (
    <div className="flex min-h-screen flex-col">
      <SkipLink />
      <Header />
      <main id={MAIN_CONTENT_ID} tabIndex={-1} className="flex-1 focus:outline-none">
        <Outlet />
      </main>
    </div>
  )
}
