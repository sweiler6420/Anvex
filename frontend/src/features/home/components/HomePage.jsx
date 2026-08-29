import Contact from './Contact'
import Features from './Features'
import Footer from './Footer'
import Hero from './Hero'
import Pricing from './Pricing'
import Workflow from './Workflow'

/**
 * `/` — the marketing home page (ANV-32), ported from
 * `AverageInvestorWeb/src/components/home/Home.jsx`.
 *
 * ---------------------------------------------------------------------------------------
 * ## The composition
 *
 * Six sections in the old file's order: `Hero`, `Features` (`#features`), `Workflow`
 * (`#workflow`), `Pricing` (`#pricing`), `Contact` (`#contact`), `Footer`. The four
 * fragment ids are exactly the ones `components/layout/navItems.js` links from the header
 * and `Footer` links from the bottom of the page, and `HomePage.test.jsx` derives that
 * assertion from `ANONYMOUS_NAV_ITEMS` rather than restating the strings — a renamed
 * section fails the suite instead of quietly turning four nav items into no-ops.
 *
 * The old `Home.jsx` imported `useNavigate`, `useAuth`, `useState`, `useEffect` and
 * `useContext` and used none of them; it is a layout wrapper and nothing more.
 *
 * ## Not ported: `Who.jsx`, `Works.jsx` and `Home.styles.js`
 *
 * The ticket lists them. They are dead code in the old repo, and porting them would import
 * that fact rather than the page. `Who.jsx` and `Works.jsx` are ten lines each, render the
 * literal strings "Who" and "Works", and are **imported by nothing** — `Home.jsx` never
 * referenced them. Their only other content is a class string from `Home.styles.js`, which
 * is likewise imported by nothing but those two files and describes a scroll-snap layout
 * (`h-screen snap-center`) the page does not use; its own first line,
 * `import styles from "../../index.css"`, binds a stylesheet to an unused identifier.
 * There is no behaviour, no copy and no layout here to preserve. Said plainly in the ANV-32
 * report rather than quietly skipped.
 *
 * ## Not ported: `InteractiveDesktop`
 *
 * Deliberately — it needs the bin-packing window system (ANV-33) and the widgets (ANV-34),
 * and lands in ANV-35. The seam is `Workflow`'s `demo` prop; see that file.
 */
export default function HomePage() {
  return (
    // `data-testid` matches the `RoutePlaceholder` this replaces, so ANV-27's and ANV-28's
    // routing tests keep asserting "/ resolved" against the real page.
    <div data-testid="route-home" className="min-h-screen px-6 pt-20">
      <Hero />
      <Features />
      <Workflow />
      <Pricing />
      <Contact />
      <Footer />
    </div>
  )
}
