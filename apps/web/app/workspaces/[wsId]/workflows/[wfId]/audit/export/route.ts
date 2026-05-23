import { NextResponse } from 'next/server'

const API_URL = process.env.OWNEVO_KERNEL_API_URL ?? 'http://localhost:8000'

interface RouteContext {
  params: Promise<{ wsId: string; wfId: string }>
}

// Proxies GET /api/audit/export?workflow_id=<wfId> from the kernel and
// streams the filtered canonical JSON download back to the browser.
// Kept server-side so the kernel URL never reaches the client, and the
// page can use a stable relative path with the `download` attribute.
export async function GET(
  _req: Request,
  { params }: RouteContext,
): Promise<Response> {
  const { wfId } = await params
  const url = `${API_URL}/api/audit/export?workflow_id=${encodeURIComponent(wfId)}`

  let upstream: Response
  try {
    upstream = await fetch(url, { cache: 'no-store' })
  } catch {
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
  // (audit-chain-wf-<id>-<timestamp>.json) reaches the browser unchanged.
  const contentDisposition =
    upstream.headers.get('content-disposition') ??
    `attachment; filename="audit-chain-wf-${wfId.slice(0, 8)}.json"`

  return new Response(upstream.body, {
    status: 200,
    headers: {
      'content-type': 'application/json',
      'content-disposition': contentDisposition,
      'cache-control': 'no-store',
    },
  })
}
