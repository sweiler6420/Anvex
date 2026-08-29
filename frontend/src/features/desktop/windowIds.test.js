import { beforeEach, describe, expect, it } from 'vitest'

import { nextWindowId, resetWindowIdCounter } from './windowIds'

/**
 * ANV-33 — window ids.
 *
 * **These tests prove real behaviour**, and the first one is the regression: it fails against
 * the original's `win_${Date.now()}`, because a loop of this speed does not advance the clock.
 */

beforeEach(resetWindowIdCounter)

describe('nextWindowId', () => {
  it('never repeats, however fast it is called', () => {
    const ids = Array.from({ length: 1000 }, () => nextWindowId())

    expect(new Set(ids).size).toBe(1000)
  })

  it('counts from one', () => {
    expect([nextWindowId(), nextWindowId(), nextWindowId()]).toEqual(['win_1', 'win_2', 'win_3'])
  })

  it('starts again after a reset, so a test can name the ids it expects', () => {
    nextWindowId()
    nextWindowId()

    resetWindowIdCounter()

    expect(nextWindowId()).toBe('win_1')
  })
})
