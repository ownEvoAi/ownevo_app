'use client'

import { useEffect, useState } from 'react'

// Mirrors the inline <script> in the static mocks: read theme from
// localStorage on mount, flip on click, persist. Server renders the
// default (light) without flicker because we only update the
// attribute after hydration.
const STORAGE_KEY = 'ownevo-theme'

export function ThemeToggle() {
  const [theme, setTheme] = useState<'light' | 'dark'>('light')

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'dark' || stored === 'light') {
      setTheme(stored)
      document.documentElement.setAttribute('data-theme', stored)
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
