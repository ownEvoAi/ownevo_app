import { NextResponse } from 'next/server'

const API_URL = process.env.OWNEVO_KERNEL_API_URL ?? 'http://localhost:8000'

interface RouteContext {
  params: Promise<{ wsId: string; wfId: string }>
}

// Downloads all eval cases for a workflow as a JSON file.
// Useful for operator review, backup, or porting cases to another deployment.
export async function GET(_req: Request, { params }: RouteContext): Promise<Response> {
  const { wfId } = await params

  let upstream: Response
  try {
    upstream = await fetch(
      `${API_URL}/api/workflows/${encodeURIComponent(wfId)}/eval-cases`,
      { cache: 'no-store' },
    )
  } catch {
    return NextResponse.json({ error: 'Kernel API not reachable.' }, { status: 502 })
  }

  if (!upstream.ok) {
    const detail = await upstream.text().catch(() => upstream.statusText)
    return NextResponse.json(
      { error: 'Kernel API error.', status: upstream.status, detail },
      { status: upstream.status },
    )
  }

  const data = await upstream.json()
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  const filename = `evals-${wfId.slice(0, 8)}-${timestamp}.json`
  const body = JSON.stringify(data, null, 2)

  return new Response(body, {
    status: 200,
    headers: {
      'content-type': 'application/json',
      'content-disposition': `attachment; filename="${filename}"`,
      'cache-control': 'no-store',
    },
  })
}
