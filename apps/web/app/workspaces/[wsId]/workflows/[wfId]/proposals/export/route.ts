import { NextResponse } from 'next/server'

const API_URL = process.env.OWNEVO_KERNEL_API_URL ?? 'http://localhost:8000'

interface RouteContext {
 params: Promise<{ wsId: string; wfId: string }>
}

// Downloads the full improvement history for a workflow — every proposal
// with its state, gate score, skill diff, decision, and rationale.
// The limit=500 cap matches the kernel's MAX_LIMIT for list_proposals.
export async function GET(
 _req: Request,
 { params }: RouteContext,
): Promise<Response> {
 const { wfId } = await params

 let upstream: Response
 try {
 upstream = await fetch(
 `${API_URL}/api/proposals?workflow_id=${encodeURIComponent(wfId)}&limit=500`,
 { cache: 'no-store' },
 )
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

 const data = await upstream.json()
 const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
 const filename = `proposals-${wfId.slice(0, 8)}-${timestamp}.json`

 return new Response(JSON.stringify(data, null, 2), {
 status: 200,
 headers: {
 'content-type': 'application/json',
 'content-disposition': `attachment; filename="${filename}"`,
 'cache-control': 'no-store',
 },
 })
}
