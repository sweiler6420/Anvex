import { Outlet } from '@tanstack/react-router'

import Header from './Header'

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
 * viewport and the footer ANV-32 adds has somewhere to sit.
 *
 * There is no skip link, and that is a decision rather than an omission. The header exposes
 * at most nine tab stops, and the obvious implementation — `<a href="#main-content">` —
 * would push a `#main-content` fragment into the router's location, which is the same
 * location `Header` reads to decide which marketing item is current: pressing skip would
 * un-highlight the nav. It becomes worth solving in ANV-32, when the home page grows long
 * enough for skipping to be worth something.
 */
export default function Layout() {
  return (
    <div className="flex min-h-screen flex-col">
      <Header />
      <main id="main-content" className="flex-1">
        <Outlet />
      </main>
    </div>
  )
}
