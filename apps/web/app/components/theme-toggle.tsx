'use client'

import { useEffect, useState } from 'react'

// Reads the theme from <html data-theme>, which the inline bootstrap
// script in app/layout.tsx applies BEFORE paint based on
// localStorage. That kills the "light flash on every navigation"
// regression dark-mode users were hitting; the toggle now just
// flips and persists.
const STORAGE_KEY = 'ownevo-theme'

export function ThemeToggle() {
  // Always renders "light" on the server, then syncs to the actual
  // applied theme on mount. Label briefly says "Dark mode" before
  // the effect runs even in dark mode; that's one render frame and
  // doesn't cause a visible flash because the page itself is
  // already painted correctly.
  const [theme, setTheme] = useState<'light' | 'dark'>('light')

  useEffect(() => {
    const applied = document.documentElement.getAttribute('data-theme')
    if (applied === 'dark' || applied === 'light') {
      setTheme(applied)
    }
  }, [])

  function toggle() {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    document.documentElement.setAttribute('data-theme', next)
    window.localStorage.setItem(STORAGE_KEY, next)
  }

  const label = theme === 'dark' ? 'Light mode' : 'Dark mode'
  return (
    <button
      type="button"
      onClick={toggle}
      className="nav-item theme-toggle-nav"
      style={{
        background: 'transparent',
        border: 0,
        width: '100%',
        textAlign: 'left',
        cursor: 'pointer',
      }}
    >
      <svg className="nav-icon" viewBox="0 0 16 16" aria-hidden>
        <path d="M13 9 A5.5 5.5 0 0 1 7 3 A5.5 5.5 0 1 0 13 9 Z" />
      </svg>
      <span className="nav-label">{label}</span>
    </button>
  )
}
