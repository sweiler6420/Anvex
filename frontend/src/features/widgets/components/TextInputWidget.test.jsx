import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import TextInputWidget from './TextInputWidget'

/**
 * ANV-34 — the echo input.
 *
 * **These tests prove real behaviour.** No measurement, no network.
 *
 * The first one is the port's whole point: the original's only accessible name was its
 * `placeholder`, which vanishes the moment a character is typed. `getByRole('textbox',
 * {name: 'Text to echo'})` fails against the original markup and passes against this one.
 */

describe('TextInputWidget', () => {
  it('gives the input a name that survives being typed into', async () => {
    const user = userEvent.setup()
    render(<TextInputWidget />)

    const input = screen.getByRole('textbox', { name: 'Text to echo' })
    await user.type(input, 'NVDA')

    // Still findable by the same name — a placeholder would not be.
    expect(screen.getByRole('textbox', { name: 'Text to echo' })).toHaveValue('NVDA')
  })

  it('echoes what is typed', async () => {
    const user = userEvent.setup()
    render(<TextInputWidget />)

    await user.type(screen.getByRole('textbox'), 'hello')

    expect(screen.getByTestId('text-input-echo')).toHaveTextContent('hello')
  })

  it('says "nothing typed yet" rather than announcing a dash', () => {
    render(<TextInputWidget />)
    expect(screen.getByRole('status')).toHaveTextContent('nothing typed yet')
  })

  it('is a live region, so the echo reaches a reader without being hunted for', async () => {
    const user = userEvent.setup()
    render(<TextInputWidget />)

    await user.type(screen.getByRole('textbox'), 'x')

    expect(screen.getByRole('status')).toHaveTextContent('x')
  })

  it('is named as a region', () => {
    render(<TextInputWidget label="Notes" />)
    expect(screen.getByRole('region', { name: 'Notes' })).toBeInTheDocument()
  })
})
