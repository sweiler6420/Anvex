import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import CounterWidget from './CounterWidget'

/**
 * ANV-34 — the counter.
 *
 * **These tests prove real behaviour.** The widget has no measurement and no network, so
 * nothing here is fabricated: a click really is a click and the count really is the count.
 * Only the *appearance* is out of reach under jsdom, and nothing below asserts on it.
 */

describe('CounterWidget', () => {
  it('is named, so a screen reader can find it among five other widgets', () => {
    render(<CounterWidget />)
    expect(screen.getByRole('region', { name: 'Counter' })).toBeInTheDocument()
  })

  it('counts up and down from the given start', async () => {
    const user = userEvent.setup()
    render(<CounterWidget initialCount={5} />)

    expect(screen.getByTestId('counter-value')).toHaveTextContent('5')

    await user.click(screen.getByRole('button', { name: 'Increase count' }))
    expect(screen.getByTestId('counter-value')).toHaveTextContent('6')

    await user.click(screen.getByRole('button', { name: 'Decrease count' }))
    await user.click(screen.getByRole('button', { name: 'Decrease count' }))
    expect(screen.getByTestId('counter-value')).toHaveTextContent('4')
  })

  it('works from the keyboard', async () => {
    // ANV-29's rule: `user.click` passes on a `<div role="button" onClick>` shim, and this
    // does not. The originals' controls were already `<button>`s but had no `type`, so
    // inside a form every press would have submitted it instead.
    const user = userEvent.setup()
    render(<CounterWidget />)

    const increase = screen.getByRole('button', { name: 'Increase count' })
    increase.focus()
    await user.keyboard('{Enter}')

    expect(screen.getByTestId('counter-value')).toHaveTextContent('1')
    expect(increase).toHaveAttribute('type', 'button')
  })

  it('announces the value through a live region', () => {
    render(<CounterWidget />)
    // The glyph buttons say nothing about the result, so without this the only feedback for
    // a press is a number the reader has to go and look for.
    expect(screen.getByRole('status')).toHaveTextContent('0')
  })

  it('gives its buttons names a reader can act on, not punctuation', () => {
    render(<CounterWidget />)
    const names = screen.getAllByRole('button').map((b) => b.getAttribute('aria-label'))
    expect(names).toEqual(['Decrease count', 'Increase count'])
  })
})
