'use client'

import { useRouter } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'

// 8.5.3(a) — review page auto-advances to the workflow detail page
// after a short delay unless the reviewer cancels. The countdown is
// the load-bearing "we trust NL-gen, but you can still bail" beat;
// cancel-on-keypress means any glance-and-stay action stops the timer.
//
// 8.5.3(c) — ⌘↵ / Ctrl-↵ from anywhere on the page triggers Confirm
// immediately, so a keyboard-driven reviewer never has to mouse over.
//
// We render the Confirm CTA itself + a small inline countdown badge
// next to it; clicking the badge cancels (same as keypress). Once
// cancelled, the timer doesn't restart — reviewer has signalled intent.
//
// AUTO_ADVANCE_SECONDS matches the PLAN 8.5.3 budget (10 s of the 90 s
// cold-start wall clock).
const AUTO_ADVANCE_SECONDS = 10

export function ConfirmButton({ continueHref }: { continueHref: string }) {
  const router = useRouter()
  const [remaining, setRemaining] = useState(AUTO_ADVANCE_SECONDS)
  const [cancelled, setCancelled] = useState(false)
  const advancedRef = useRef(false)
  // Screen-reader announcement — updated only on mount, cancel, and advance.
  // The pill itself is aria-hidden so the per-tick number doesn't broadcast.
  const [srAnnounce, setSrAnnounce] = useState(
    `Auto-confirming in ${AUTO_ADVANCE_SECONDS} seconds. Press any key or click to cancel.`,
  )

  // Single canonical "go to workflow page" — used by manual click, ⌘↵,
  // and the countdown. Guards against double-fire if the user clicks
  // mid-tick.
  const advance = () => {
    if (advancedRef.current) return
    advancedRef.current = true
    setCancelled(true)
    setSrAnnounce('Confirmed. Opening workflow.')
    router.push(continueHref)
  }

  // Cancelling stops the countdown but does NOT lock the button — the
  // reviewer can still Confirm afterward (manually or via ⌘↵).
  const cancel = () => {
    setCancelled(true)
    setSrAnnounce('Auto-confirm cancelled.')
  }

  // Countdown only ticks while the tab is visible — prevents a background
  // tab (opened via Cmd+click) from auto-navigating before the reviewer
  // ever sees the spec.
  useEffect(() => {
    if (cancelled) return

    let tick: ReturnType<typeof setInterval> | null = null

    const start = () => {
      if (tick !== null) return
      tick = setInterval(() => {
        setRemaining((r) => {
          if (r <= 1) {
            clearInterval(tick!)
            tick = null
            advance()
            return 0
          }
          return r - 1
        })
      }, 1000)
    }

    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        document.removeEventListener('visibilitychange', onVisible)
        start()
      }
    }

    if (document.visibilityState === 'visible') {
      start()
    } else {
      document.addEventListener('visibilitychange', onVisible)
    }

    return () => {
      if (tick !== null) clearInterval(tick)
      document.removeEventListener('visibilitychange', onVisible)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cancelled])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // ⌘↵ / Ctrl-↵ from anywhere on the page = Confirm immediately,
      // unless focus is inside a text field (e.g. an eval-case input).
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
        if ((e.target as HTMLElement).closest('input, textarea, [contenteditable]'))
          return
        e.preventDefault()
        advance()
        return
      }
      // Any other key cancels the auto-advance. The reviewer is
      // engaging with the page; don't yank it out from under them.
      if (!cancelled) cancel()
    }
    // Pointer / wheel interactions also count as "I'm still looking".
    const onPointer = () => {
      if (!cancelled) cancel()
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('wheel', onPointer, { passive: true })
    window.addEventListener('mousedown', onPointer)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('wheel', onPointer)
      window.removeEventListener('mousedown', onPointer)
    }
  }, [cancelled])

  return (
    <>
      {/* Announce once at mount, cancel, and advance — not on every tick. */}
      <span
        role="status"
        aria-live="polite"
        aria-atomic="true"
        style={{
          position: 'absolute',
          width: '1px',
          height: '1px',
          padding: 0,
          margin: '-1px',
          overflow: 'hidden',
          clip: 'rect(0,0,0,0)',
          whiteSpace: 'nowrap',
          border: 0,
        }}
      >
        {srAnnounce}
      </span>
      <div className="confirm-cluster">
        {!cancelled && remaining > 0 ? (
          <span
            className="auto-advance-pill"
            aria-hidden="true"
            onClick={cancel}
            title="Click or press any key to cancel"
          >
            Confirming in <strong>{remaining}s</strong>… press any key to
            stay
          </span>
        ) : null}
        <button
          type="button"
          className="btn btn-primary"
          onClick={advance}
          aria-keyshortcuts="Meta+Enter Control+Enter"
        >
          Looks good · open workflow &rsaquo;
        </button>
        <span className="kbd-hint">
          <kbd>⌘</kbd>
          <kbd>↵</kbd> to confirm
        </span>
      </div>
    </>
  )
}
