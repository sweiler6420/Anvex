import React from 'react'
import ReactDOM from 'react-dom/client'

import App from './App'
import { ErrorsProvider } from './providers/ErrorsProvider'
import { ThemeProvider } from './providers/ThemeProvider'
import './styles/index.css'

/**
 * The browser entry point, and nothing else.
 *
 * `ThemeProvider` is outermost because it owns the `dark` class on `<html>` and nothing
 * below it may render before that class is decided. `ErrorsProvider` sits inside it so the
 * surface that eventually renders an error (ANV-28) is themed.
 *
 * `App` (ANV-27) owns everything below: `AuthProvider` — still inside `ErrorsProvider`, so
 * anything it raises has somewhere to go — with the router beneath it and the `onSignOut`
 * redirect wired between the two. That composition lives in a component rather than here
 * because this file calls `createRoot` and cannot be imported by a test; see `App.jsx`.
 */
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ThemeProvider>
      <ErrorsProvider>
        <App />
      </ErrorsProvider>
    </ThemeProvider>
  </React.StrictMode>,
)
