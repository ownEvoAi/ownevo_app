import { NextResponse } from 'next/server'

const API_URL = process.env.OWNEVO_KERNEL_API_URL ?? 'http://localhost:8000'

interface RouteContext {
  params: Promise<{ wsId: string; wfId: string }>
}

// Full portable export of a workflow's ownership record:
//   workflow   — anatomy (description, mode, agent_model_id)
//   skills     — every skill with its head-version content
//   eval_cases — the regression test suite
//   proposals  — the full improvement history
//   failures   — production failure clusters that drove improvements
//   audit      — workflow-scoped append-only audit trail
//
// Intended for operator backup, compliance review, and migration to
// another deployment. The `generated_at` timestamp marks when the
// snapshot was taken.
export async function GET(
  _req: Request,
  { params }: RouteContext,
): Promise<Response> {
  const { wfId } = await params
  const wfEnc = encodeURIComponent(wfId)

  // Fan out all fetches in parallel — anatomy + skill list can resolve
  // independently of evals / proposals / failures / audit.
  let anatomy: unknown,
    skillList: { items: Array<{ id: string }> },
    evalCases: unknown,
    proposals: unknown,
    failures: unknown,
    audit: unknown

  try {
    const [
      anatomyRes,
      skillsRes,
      evalsRes,
      proposalsRes,
      failuresRes,
      auditRes,
    ] = await Promise.all([
      fetch(`${API_URL}/api/workflows/${wfEnc}`, { cache: 'no-store' }),
      fetch(`${API_URL}/api/workflows/${wfEnc}/skills`, { cache: 'no-store' }),
      fetch(`${API_URL}/api/workflows/${wfEnc}/eval-cases`, { cache: 'no-store' }),
      fetch(`${API_URL}/api/proposals?workflow_id=${wfEnc}&limit=500`, { cache: 'no-store' }),
      fetch(`${API_URL}/api/workflows/${wfEnc}/failure_clusters`, { cache: 'no-store' }),
      fetch(`${API_URL}/api/audit?workflow_id=${wfEnc}&limit=500`, { cache: 'no-store' }),
    ])

    if (!anatomyRes.ok) {
      return NextResponse.json({ error: 'Workflow not found.' }, { status: anatomyRes.status })
    }

    // Parse all responses — treat non-critical fetch failures as empty
    // so a single missing dataset doesn't abort the whole bundle.
    const safeJson = async (res: Response) => res.ok ? res.json() : null

    ;[anatomy, skillList, evalCases, proposals, failures, audit] =
      await Promise.all([
        anatomyRes.json(),
        skillsRes.ok ? skillsRes.json() : { items: [] },
        safeJson(evalsRes),
        safeJson(proposalsRes),
        safeJson(failuresRes),
        safeJson(auditRes),
      ])
  } catch {
    return NextResponse.json({ error: 'Kernel API not reachable.' }, { status: 502 })
  }

  // Enrich skills with full head-version content.
  let skills: unknown[] = []
  try {
    const items = (skillList as { items: Array<{ id: string }> }).items ?? []
    skills = await Promise.all(
      items.map(async (s) => {
        const res = await fetch(`${API_URL}/api/skills/${encodeURIComponent(s.id)}`, {
          cache: 'no-store',
        })
        return res.ok ? res.json() : { id: s.id, error: 'not_found' }
      }),
    )
  } catch {
    // Skills fetch failed — include empty array rather than aborting.
    skills = []
  }

  const generatedAt = new Date().toISOString()
  const timestamp = generatedAt.replace(/[:.]/g, '-').slice(0, 19)
  const filename = `bundle-${wfId.slice(0, 8)}-${timestamp}.json`

  const bundle = {
    generated_at: generatedAt,
    workflow: anatomy,
    skills,
    eval_cases: evalCases,
    proposals,
    failures,
    audit,
  }

  return new Response(JSON.stringify(bundle, null, 2), {
    status: 200,
    headers: {
      'content-type': 'application/json',
      'content-disposition': `attachment; filename="${filename}"`,
      'cache-control': 'no-store',
    },
  })
}
