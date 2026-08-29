import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import App from './App'
import { API_BASE_URL } from './lib/env'

describe('App', () => {
  it('renders the shell', () => {
    render(<App />)

    expect(screen.getByRole('heading', { level: 1, name: 'Anvex' })).toBeInTheDocument()
    expect(screen.getByText('Investment research platform')).toBeInTheDocument()
  })

  it('renders the carried-over logo through SVGR', () => {
    render(<App />)

    // `?react` gave us a component, not a URL — an <svg> in the DOM rather than an <img>.
    const logo = screen.getByRole('img', { name: 'Anvex logo' })
    expect(logo.tagName.toLowerCase()).toBe('svg')
  })

  it('applies the carried-over Tailwind tokens', () => {
    render(<App />)

    const heading = screen.getByRole('heading', { level: 1, name: 'Anvex' })
    // font-gothic -> RTFont, text-4xl -> the compressed scale, brand-* -> cyan,
    // dark: -> the class-based dark mode. If any of those disappear from the config the
    // class names below are still emitted but style nothing, so tailwind.test.js checks
    // the generated CSS; this checks that the component still asks for them.
    expect(heading).toHaveClass('font-gothic', 'text-4xl', 'text-brand-600', 'dark:text-brand-400')
  })

  it('reads the API base URL from the root .env, not from a second config file', () => {
    render(<App />)

    const shown = screen.getByTestId('api-base-url').textContent
    expect(shown).toBe(API_BASE_URL || '(same origin — via the dev proxy)')
  })
})
