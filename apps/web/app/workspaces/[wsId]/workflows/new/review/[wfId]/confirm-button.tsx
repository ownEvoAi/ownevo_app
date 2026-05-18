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

  // Single canonical "go to workflow page" — used by manual click, ⌘↵,
  // and the countdown. Guards against double-fire if the user clicks
  // mid-tick.
  const advance = () => {
    if (advancedRef.current) return
    advancedRef.current = true
    setCancelled(true)
    router.push(continueHref)
  }

  const cancel = () => {
    setCancelled(true)
  }

  useEffect(() => {
    if (cancelled) return

    const tick = setInterval(() => {
      setRemaining((r) => {
        if (r <= 1) {
          clearInterval(tick)
          advance()
          return 0
        }
        return r - 1
      })
    }, 1000)

    return () => clearInterval(tick)
    // advance is a stable closure over continueHref; we intentionally
    // don't include it in deps to avoid resetting the timer per render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cancelled])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // ⌘↵ / Ctrl-↵ from anywhere on the page = Confirm immediately.
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
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
    <div className="confirm-cluster">
      {!cancelled && remaining > 0 ? (
        <span
          className="auto-advance-pill"
          role="status"
          aria-live="polite"
          aria-label={`Confirming in ${remaining} seconds`}
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
  )
}
