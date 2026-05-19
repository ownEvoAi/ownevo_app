import Link from 'next/link'
import { getTemplate, type VerticalTemplate } from '../templates'
import { DesignFlow } from './design-flow'
import { loadNextQuestion, type NextQuestionState } from './actions'

const EMPTY_QUESTION_STATE: NextQuestionState = {
  loaded: false,
  next: null,
  done: false,
  totalQuestions: 0,
  answeredCount: 0,
  error: null,
}

interface PageProps {
  params: Promise<{ wsId: string }>
  searchParams: Promise<{ template_id?: string; description?: string }>
}

// Track 9.1.2 — conversational authoring surface. The operator either
// (a) clicked "Run discovery" from a template card on /workflows/new, or
// (b) wrote a free-form description and chose to go through the design
// agent before generation. The design agent asks 2–5 questions (metric,
// ambiguity, optionally trigger / surface / premise from the generic
// fallback) and the operator's answers append to the description before
// it hits POST /api/nl-gen/generate. Once the kernel returns done=true,
// the Generate button enables and routes to the existing review page.
//
// Three-pane layout:
//   left   — description + template card (read-only here; edit on /new)
//   centre — chat panel (the discovery interview)
//   right  — in-progress transcript + Generate button
//
// 9.1.4 shipped the kernel-side audit-chain mirror (`design-agent-negotiation`
// kind, migration 0012). The web side (`generateWithDiscoveryAction`) does not
// yet pass `design_agent_log` to the kernel — the JSONB column stays NULL and
// audit entries are not written until that wire-up lands.
export default async function DesignAgentPage({
  params,
  searchParams,
}: PageProps) {
  const [{ wsId }, sp] = await Promise.all([params, searchParams])

  const templateId = sp.template_id ?? null
  const template: VerticalTemplate | undefined = templateId
    ? getTemplate(templateId)
    : undefined

  // Description priority:
  //   1. explicit `?description=` (operator typed something on /new and
  //      clicked "Run discovery" — preserve their text)
  //   2. template's sample_description (template-anchored entry)
  //   3. empty string (generic / free-form entry — left pane will warn)
  const description = (sp.description ?? template?.sample_description ?? '').trim()

  // Pre-fetch question #0 so the chat panel doesn't flash an empty state
  // on first paint. Subsequent questions load client-side via the server
  // action. The LLM interviewer runs Sonnet 4.6 which is ~6-12s end to
  // end; budget 25s so a normal call lands before SSR returns. The
  // client side retries with no timeout when the SSR pre-fetch errors
  // out (slow kernel / Anthropic hiccup), so users never see a stuck
  // blank panel.
  const initialQuestion =
    description.length > 0
      ? await loadNextQuestion({
          description,
          templateId,
          priorAnswers: [],
          signal: AbortSignal.timeout(25000),
        })
      : EMPTY_QUESTION_STATE

  return (
    <div className="design-wrap">
      <header className="gen-head">
        <h1 className="gen-title">Design with the agent</h1>
        <p className="gen-sub">
          A short discovery interview before generation &mdash; the design
          agent surfaces the metric trade-off and one or two ambiguities
          most workflows miss on the first pass. Answer or skip each
          question; the answers are recorded on the workflow so a future
          reviewer can see the deliberate choices.
        </p>
        <p className="design-back-row">
          <Link
            href={`/workspaces/${wsId}/workflows/new${(() => {
              const qs = new URLSearchParams()
              if (templateId) qs.set('template_id', templateId)
              if (description) qs.set('description', description)
              const s = qs.toString()
              return s ? `?${s}` : ''
            })()}`}
            className="design-back-link"
          >
            &lsaquo; Back to description
          </Link>
        </p>
      </header>

      {description.length === 0 ? (
        <div
          role="alert"
          className="api-banner"
          style={{ marginTop: 12 }}
        >
          <strong>No description yet.</strong> Go back, write a workflow
          description (or pick a template), then return here to run
          discovery.
        </div>
      ) : (
        <DesignFlow
          wsId={wsId}
          description={description}
          templateId={templateId}
          templateName={template?.name ?? null}
          templatePersona={template?.persona ?? null}
          initialState={initialQuestion}
        />
      )}
    </div>
  )
}
