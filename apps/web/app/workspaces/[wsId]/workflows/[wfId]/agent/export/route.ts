import { NextResponse } from 'next/server'

const API_URL = process.env.OWNEVO_KERNEL_API_URL ?? 'http://localhost:8000'

interface RouteContext {
  params: Promise<{ wsId: string; wfId: string }>
}

// Builds a JSON bundle of the workflow's current agent configuration:
// the anatomy (description, mode, agent_model_id) plus every skill's
// head-version content. Intended for operator backup and portability.
export async function GET(_req: Request, { params }: RouteContext): Promise<Response> {
  const { wfId } = await params

  let anatomy: unknown
  let skillList: { items: Array<{ id: string }> }
  try {
    const [anatomyRes, skillsRes] = await Promise.all([
      fetch(`${API_URL}/api/workflows/${encodeURIComponent(wfId)}`, { cache: 'no-store' }),
      fetch(`${API_URL}/api/workflows/${encodeURIComponent(wfId)}/skills`, { cache: 'no-store' }),
    ])
    if (!anatomyRes.ok) {
      return NextResponse.json({ error: 'Workflow not found.' }, { status: anatomyRes.status })
    }
    if (!skillsRes.ok) {
      return NextResponse.json({ error: 'Could not load skills.' }, { status: skillsRes.status })
    }
    anatomy = await anatomyRes.json()
    skillList = await skillsRes.json()
  } catch {
    return NextResponse.json({ error: 'Kernel API not reachable.' }, { status: 502 })
  }

  let skills: unknown[]
  try {
    skills = await Promise.all(
      skillList.items.map(async (s) => {
        const res = await fetch(`${API_URL}/api/skills/${encodeURIComponent(s.id)}`, { cache: 'no-store' })
        return res.ok ? res.json() : { id: s.id, error: 'not_found' }
      }),
    )
  } catch {
    return NextResponse.json({ error: 'Kernel API not reachable.' }, { status: 502 })
  }

  const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  const filename = `agent-${wfId.slice(0, 8)}-${timestamp}.json`
  const body = JSON.stringify({ workflow: anatomy, skills }, null, 2)

  return new Response(body, {
    status: 200,
    headers: {
      'content-type': 'application/json',
      'content-disposition': `attachment; filename="${filename}"`,
      'cache-control': 'no-store',
    },
  })
}
