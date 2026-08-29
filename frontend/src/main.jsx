import React from 'react'
import ReactDOM from 'react-dom/client'

import App from './App'
import { ErrorsProvider } from './providers/ErrorsProvider'
import { ThemeProvider } from './providers/ThemeProvider'
import './styles/index.css'

/**
 * `ThemeProvider` is outermost because it owns the `dark` class on `<html>` and nothing
 * below it may render before that class is decided. `ErrorsProvider` sits inside it so the
 * surface that eventually renders an error (ANV-28) is themed.
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
