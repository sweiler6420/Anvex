import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import StaticInfoWidget from './StaticInfoWidget'

/**
 * ANV-34 — the static text widget.
 *
 * **These tests prove real behaviour.** There is nothing environmental in this component.
 */

describe('StaticInfoWidget', () => {
  it('is named', () => {
    render(<StaticInfoWidget />)
    expect(screen.getByRole('region', { name: 'Info' })).toBeInTheDocument()
  })

  it('renders the text it is given rather than a sentence baked into it', () => {
    render(<StaticInfoWidget text="Two hundred candles, oldest first." />)
    expect(screen.getByText('Two hundred candles, oldest first.')).toBeInTheDocument()
  })

  it('has a default so it is droppable with no props at all', () => {
    render(<StaticInfoWidget />)
    expect(screen.getByRole('region', { name: 'Info' }).textContent).not.toBe('')
  })

  it('takes its accessible name from the label prop', () => {
    render(<StaticInfoWidget label="Release notes" />)
    expect(screen.getByRole('region', { name: 'Release notes' })).toBeInTheDocument()
  })
})
