import Logo from './assets/Changed_Logo.svg?react'
import { API_BASE_URL } from './lib/env'

/**
 * The scaffold shell (ANV-23).
 *
 * Deliberately static and deliberately small: ANV-25 replaces it with the TanStack Router
 * tree (CLAUDE.md §5) and ANV-28..36 port the real screens. What it is here for is to
 * prove the whole pipeline end to end — JSX through @vitejs/plugin-react, SVGR through
 * `?react`, the carried-over Tailwind tokens (`font-gothic`, `text-4xl`, `brand-*`,
 * `dark:`), the self-hosted RTFont, and the root `.env` reaching the bundle as
 * `import.meta.env.VITE_API_BASE_URL`.
 */
export default function App() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-neutral-50 p-8 text-neutral-900 dark:bg-neutral-950 dark:text-neutral-300">
      <Logo className="h-16 w-16 text-brand-600" role="img" aria-label="Anvex logo" />

      <h1 className="font-gothic text-4xl font-xl text-brand-600 dark:text-brand-400">Anvex</h1>

      <p className="font-base text-base text-neutral-600 dark:text-neutral-400">
        Investment research platform
      </p>

      <dl className="text-sm text-neutral-500">
        <dt className="inline">API base URL: </dt>
        <dd className="inline font-medium" data-testid="api-base-url">
          {API_BASE_URL || '(same origin — via the dev proxy)'}
        </dd>
      </dl>
    </main>
  )
}
