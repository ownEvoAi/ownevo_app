// Custom 404 page for the Pages Router (overrides Next.js default _error.js).
// This prevents the useContext-null error during static prerendering that
// occurs when the default _error.js is prerendered without a React root.
export default function Custom404() {
  return (
    <div style={{ padding: 48, textAlign: 'center' }}>
      <h1>404 — Page not found</h1>
    </div>
  )
}
