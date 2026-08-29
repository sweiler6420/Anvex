import { createContext } from 'react'

/**
 * The auth context (ANV-26). Three files per provider, per ANV-25: the `createContext` call
 * lives here on its own so `hooks/useAuth.js` can reach it without importing the component,
 * and so `AuthProvider.jsx` keeps React Fast Refresh.
 *
 * `null` by default, and `useAuth` throws on it. The old `AuthProvider.js` defaulted the
 * context to `{}`, which meant a component rendered outside the provider destructured
 * `setAuth` as `undefined` and failed later, somewhere else, with no mention of a provider.
 *
 * **There is deliberately no `accessToken` on this value.** Nothing in the UI should read
 * the token: the transport reads it through `installTokenStore` (ANV-24), which is what
 * keeps a single copy of it and what lets the storage policy change without touching a
 * component. A component that thinks it needs the token wants a call in a feature's
 * `api.js` instead.
 *
 * @type {import('react').Context<{
 *   isAuthenticated: boolean,
 *   login: (credentials: {username: string, password: string}) => Promise<void>,
 *   logout: () => void,
 *   restore: () => boolean,
 * } | null>}
 */
export const AuthContext = createContext(null)

export default AuthContext
