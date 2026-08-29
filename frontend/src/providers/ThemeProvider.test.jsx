import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDarkMode } from '@hooks/useDarkMode'

import { ThemeProvider, THEME_STORAGE_KEY } from './ThemeProvider'

/**
 * A probe that renders the whole context, so an assertion never has to reach into React.
 */
function Probe() {
  const { theme, isDark, setTheme, toggleTheme } = useDarkMode()
  return (
    <div>
      <span data-testid="theme">{theme}</span>
      <span data-testid="is-dark">{String(isDark)}</span>
      <button onClick={toggleTheme}>toggle</button>
      <button onClick={() => setTheme('light')}>go light</button>
      <button onClick={() => setTheme('chartreuse')}>go nonsense</button>
    </div>
  )
}

const root = () => document.documentElement

/** A `matchMedia` that answers one way. jsdom's own is not something to depend on. */
function stubMatchMedia(matches) {
  window.matchMedia = vi.fn((query) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

beforeEach(() => {
  window.localStorage.clear()
  root().className = ''
  stubMatchMedia(false)
})

afterEach(() => {
  delete window.matchMedia
  window.localStorage.clear()
  root().className = ''
})

describe('ThemeProvider', () => {
  it('puts the current theme class on the root element and persists it', () => {
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('light')
    expect(root()).toHaveClass('light')
    expect(root()).not.toHaveClass('dark')

    fireEvent.click(screen.getByRole('button', { name: 'toggle' }))

    expect(screen.getByTestId('theme')).toHaveTextContent('dark')
    expect(screen.getByTestId('is-dark')).toHaveTextContent('true')
    // Tailwind's `darkMode: 'class'` reads exactly this.
    expect(root()).toHaveClass('dark')
    expect(root()).not.toHaveClass('light')
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')
  })

  it('touches storage when it renders, not when it is imported', async () => {
    // The old provider read `localStorage` in a module-level `const`, so the *import*
    // touched storage — before any error boundary existed, and in any environment without
    // a `window`. `resetModules` + a fresh dynamic import is what makes that observable:
    // the static import at the top of this file happened before the spy could exist.
    vi.resetModules()
    const getItem = vi.spyOn(Storage.prototype, 'getItem')

    await import('./ThemeProvider')
    expect(getItem).not.toHaveBeenCalled()

    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    )
    expect(getItem).toHaveBeenCalledWith(THEME_STORAGE_KEY)
  })

  it('restores a stored theme on mount', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'dark')

    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('dark')
    expect(root()).toHaveClass('dark')
  })

  it('follows prefers-color-scheme when nothing is stored', () => {
    stubMatchMedia(true)

    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('dark')
    expect(root()).toHaveClass('dark')
  })

  it('lets a stored choice beat the OS preference', () => {
    // The point of consulting the OS only when nothing is stored: a user who chose light
    // on a dark machine gets light, on every visit.
    stubMatchMedia(true)
    window.localStorage.setItem(THEME_STORAGE_KEY, 'light')

    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('light')
  })

  it('falls back to light when the browser will not answer matchMedia', () => {
    delete window.matchMedia

    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('light')
  })

  it('leaves exactly one theme class on the root, through every transition', () => {
    // **This test does not fail on the old implementation, and saying so is the point.**
    // `classList.remove(theme === 'dark' ? 'light' : 'dark')` and
    // `classList.remove('light', 'dark')` are behaviourally identical while `light` and
    // `dark` are the only two themes: the old form is a latent hazard — it would leave a
    // third theme's class on `<html>` forever — not a live defect, and there is no honest
    // way to make a test fail on it without first adding the third theme.
    //
    // What is worth pinning is the invariant the rewrite makes structural: whatever the
    // root started with, exactly one theme class is on it afterwards. A regression that
    // stops removing, or starts adding two, fails here.
    root().classList.add('light', 'dark', 'unrelated-app-class')

    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    )

    const themeClasses = () => ['light', 'dark'].filter((c) => root().classList.contains(c))

    expect(themeClasses()).toEqual(['light'])
    fireEvent.click(screen.getByRole('button', { name: 'toggle' }))
    expect(themeClasses()).toEqual(['dark'])
    fireEvent.click(screen.getByRole('button', { name: 'toggle' }))
    expect(themeClasses()).toEqual(['light'])

    // And it owns *only* those classes — a class somebody else put on the root survives.
    expect(root()).toHaveClass('unrelated-app-class')
  })

  it('ignores a stored value that is not a theme', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'midnight')

    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    )

    expect(screen.getByTestId('theme')).toHaveTextContent('light')
    expect(root().className).not.toContain('midnight')
  })

  it('ignores a setTheme call with a value that is not a theme', () => {
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'go nonsense' }))

    expect(screen.getByTestId('theme')).toHaveTextContent('light')
    expect(root().className).not.toContain('chartreuse')
  })
})

describe('when storage is unavailable', () => {
  it('still renders and still themes when reading throws', () => {
    // A private window, or a browser with site data blocked: the read is the half the old
    // provider guarded, but it guarded it at module scope, where a throw is an import
    // failure with no boundary above it.
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('The operation is insecure.', 'SecurityError')
    })

    expect(() =>
      render(
        <ThemeProvider>
          <Probe />
        </ThemeProvider>,
      ),
    ).not.toThrow()

    expect(screen.getByTestId('theme')).toHaveTextContent('light')
    expect(root()).toHaveClass('light')
  })

  it('still renders and still themes when writing throws', () => {
    // Safari's private mode throws only here — the read succeeds and the write does not,
    // which is exactly the asymmetry the old empty `catch {}` was papering over.
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError', 'QuotaExceededError')
    })

    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    )

    expect(() => fireEvent.click(screen.getByRole('button', { name: 'toggle' }))).not.toThrow()

    // The class is what makes the session correct; only the next session loses it.
    expect(screen.getByTestId('theme')).toHaveTextContent('dark')
    expect(root()).toHaveClass('dark')
  })
})

describe('useDarkMode', () => {
  it('names the missing provider instead of half-working', () => {
    // React logs the thrown error too; silence it so the suite output stays readable.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<Probe />)).toThrow(/ThemeProvider/)
    spy.mockRestore()
  })
})
