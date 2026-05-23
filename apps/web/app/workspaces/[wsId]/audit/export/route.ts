import { NextResponse } from 'next/server'

const API_URL = process.env.OWNEVO_KERNEL_API_URL ?? 'http://localhost:8000'

// Proxies GET /api/audit/export from the kernel and streams the canonical
// JSON download back to the browser. Kept on a workspace-scoped URL so
// the kernel URL never leaves the server, and the page can link to a
// stable relative path with the `download` attribute.
export async function GET(): Promise<Response> {
  let upstream: Response
  try {
    upstream = await fetch(`${API_URL}/api/audit/export`, { cache: 'no-store' })
  } catch (err) {
    return NextResponse.json(
      { error: 'Kernel API not reachable.' },
      { status: 502 },
    )
  }

  if (!upstream.ok) {
    const detail = await upstream.text().catch(() => upstream.statusText)
    return NextResponse.json(
      { error: 'Kernel API error.', status: upstream.status, detail },
      { status: upstream.status },
    )
  }

  // Forward the kernel's Content-Disposition so the filename it stamped
  // (audit-chain-<timestamp>.json) reaches the browser.
  const contentDisposition =
    upstream.headers.get('content-disposition') ??
    'attachment; filename="audit-chain.json"'

  return new Response(upstream.body, {
    status: 200,
    headers: {
      'content-type': 'application/json',
      'content-disposition': contentDisposition,
      'cache-control': 'no-store',
    },
  })
}
