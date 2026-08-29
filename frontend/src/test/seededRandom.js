/**
 * A seeded pseudo-random generator for property-style tests (ANV-33).
 *
 * Lives beside `setup.js` because it is harness, not application code, and because the
 * alternative — a six-line generator copied into each test file that wants one — is the
 * shape §6 tells us not to take.
 *
 * `Math.random()` is what this exists to avoid. A property test built on it fails on one
 * developer's machine, in one run, and cannot be re-run: the counter-example is gone with
 * the process. A seed makes a failure a permanent, quotable input.
 *
 * The generator is `mulberry32` — a 32-bit counter through three mixing steps. It is not
 * cryptographic and is not trying to be; it is fast, has a long enough period for a test
 * loop, and is short enough to read.
 *
 * @param {number} seed
 * @returns {{float: () => number, int: (min: number, max: number) => number}}
 *   `float` in `[0, 1)`; `int` inclusive of both bounds.
 */
export function seededRandom(seed) {
  let state = seed >>> 0

  const float = () => {
    state = (state + 0x6d2b79f5) >>> 0
    let t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }

  return {
    float,
    int: (min, max) => min + Math.floor(float() * (max - min + 1)),
  }
}
