// Root-level 404 handler for the App Router.
// Prevents Next.js from falling back to the Pages Router pages/_error.js
// during static page generation.
export default function NotFound() {
  return (
    <div
      style={{
        minHeight: '60vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        fontFamily: 'inherit',
      }}
    >
      <h1 style={{ fontSize: 48, fontWeight: 700, margin: '0 0 12px' }}>404</h1>
      <p style={{ color: 'var(--text-muted, #6b7280)', fontSize: 16 }}>
        Page not found.
      </p>
      <a href="/" style={{ marginTop: 16, color: 'var(--accent, #3b82f6)' }}>
        ← Go home
      </a>
    </div>
  )
}
