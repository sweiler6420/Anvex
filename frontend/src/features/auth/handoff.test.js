import { describe, expect, it } from 'vitest'

import { readSignUpHandoff, SIGN_UP_HANDOFF_KEY, signUpHandoffState } from './handoff'

/**
 * The sign-up → login credentials hand-off (ANV-29).
 *
 * The builder and the reader are tested as a pair, because the failure mode of the two
 * disagreeing is a form that silently arrives empty — which looks like a design decision
 * rather than a bug. ANV-30 imports `signUpHandoffState`; `LoginPage` imports
 * `readSignUpHandoff`; nothing writes the key by hand at either end.
 */
describe('signUpHandoffState', () => {
  it('nests the credentials under one namespaced key', () => {
    expect(signUpHandoffState({ username: 'ada', password: 'hunter2' })).toEqual({
      [SIGN_UP_HANDOFF_KEY]: { username: 'ada', password: 'hunter2' },
    })
  })

  it('omits the password entirely when there is not one', () => {
    // Not `password: undefined`: this object is serialised into session history, and
    // "the key is absent" is the shape ANV-30 gets if it decides a password should not go
    // there at all.
    const state = signUpHandoffState({ username: 'ada' })

    expect(state[SIGN_UP_HANDOFF_KEY]).toEqual({ username: 'ada' })
    expect('password' in state[SIGN_UP_HANDOFF_KEY]).toBe(false)
  })
})

describe('readSignUpHandoff', () => {
  it('round-trips what the builder produced', () => {
    const credentials = { username: 'ada@example.com', password: 'correct-horse' }

    expect(readSignUpHandoff(signUpHandoffState(credentials))).toEqual(credentials)
  })

  it('fills the missing half of a username-only hand-off with an empty string', () => {
    // So the input stays *controlled*. `value={undefined}` makes React switch the field to
    // uncontrolled and warn three keystrokes later, which is a long way from the cause.
    expect(readSignUpHandoff(signUpHandoffState({ username: 'ada' }))).toEqual({
      username: 'ada',
      password: '',
    })
  })

  it.each([
    ['no state at all', undefined],
    ['null', null],
    ['a state object without the key', { __TSR_index: 0 }],
    ['a hand-off that is not an object', { [SIGN_UP_HANDOFF_KEY]: 'ada' }],
    ['a null hand-off', { [SIGN_UP_HANDOFF_KEY]: null }],
    ['a hand-off with neither half', { [SIGN_UP_HANDOFF_KEY]: {} }],
    ['non-string halves', { [SIGN_UP_HANDOFF_KEY]: { username: 7, password: {} } }],
  ])('reads %s as no hand-off', (_label, state) => {
    // Location state survives a reload and a Back press, so by the time it is read it is
    // user-reachable data and none of these may reach the form.
    expect(readSignUpHandoff(state)).toBeNull()
  })
})
