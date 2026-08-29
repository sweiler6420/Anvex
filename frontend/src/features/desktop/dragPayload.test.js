import { afterEach, describe, expect, it } from 'vitest'

import { clearPendingWindow, peekPendingWindow, setPendingWindow } from './dragPayload'

/**
 * ANV-33 — the menu-to-desktop drag hand-off.
 *
 * **These tests prove real behaviour.** The module has no DOM in it; what they are checking
 * is the contract the drop handler relies on — that `dragover` can ask repeatedly without
 * consuming, and that a finished or cancelled drag leaves nothing armed for the next one.
 */

afterEach(clearPendingWindow)

describe('the pending window', () => {
  it('is null before anything is dragged', () => {
    expect(peekPendingWindow()).toBeNull()
  })

  it('hands back exactly the object it was given, React content and all', () => {
    const template = { title: 'Counter', color: '#3b82f6', content: { type: 'div' } }

    setPendingWindow(template)

    expect(peekPendingWindow()).toBe(template)
  })

  it('does not consume on read, because `dragover` fires continuously', () => {
    setPendingWindow({ title: 'Counter' })

    expect(peekPendingWindow()).not.toBeNull()
    expect(peekPendingWindow()).not.toBeNull()
    expect(peekPendingWindow()).not.toBeNull()
  })

  it('is cleared when the drag ends, so a cancelled drag arms nothing', () => {
    setPendingWindow({ title: 'Counter' })

    clearPendingWindow()

    expect(peekPendingWindow()).toBeNull()
  })

  it('replaces rather than stacks — a second drag is not the first one', () => {
    setPendingWindow({ title: 'first' })
    setPendingWindow({ title: 'second' })

    expect(peekPendingWindow()).toMatchObject({ title: 'second' })
  })

  it('normalises undefined to null, so callers only test for one absence', () => {
    setPendingWindow(undefined)

    expect(peekPendingWindow()).toBeNull()
  })

  it('leaves nothing on the global object — the thing this module replaced', () => {
    setPendingWindow({ title: 'Counter' })

    expect(window.__BINPACKING_DRAG).toBeUndefined()
  })
})
