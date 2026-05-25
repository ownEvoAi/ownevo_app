import {
  getWorkflowAnatomy,
  kernelError,
  KernelApiError,
  listWorkflowEvalCases,
  type EvalCaseSummary,
} from '@/lib/api'
import { AddEvalCaseForm } from './add-form'
import { DeleteEvalCaseButton } from './delete-button'
import { GenerateEvalCasesButton } from './generate-button'
import { PushEvalCasesCopilotStudioButton } from './push-copilot-studio-button'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

export default async function WorkflowEvalCasesPage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let items: EvalCaseSummary[] = []
  let apiError: { title: string; detail: string } | null = null
  let notFound = false
  let origin: string | null = null

  // Run both fetches in parallel; anatomy is best-effort — a transient error
  // there must not blank the eval-cases list.
  const [listResult, anatomyResult] = await Promise.allSettled([
    listWorkflowEvalCases(wfId),
    getWorkflowAnatomy(wfId),
  ])

  if (listResult.status === 'fulfilled') {
    items = listResult.value.items
  } else if (listResult.reason instanceof KernelApiError && listResult.reason.status === 404) {
    notFound = true
  } else {
    apiError = kernelError(listResult.reason)
  }

  if (!notFound && anatomyResult.status === 'fulfilled') {
    origin = anatomyResult.value.origin
  }

  const train = items.filter((c) => !c.is_test_fold)
  const test = items.filter((c) => c.is_test_fold)

  return (
    <>
      <header className="page-header" style={{ marginBottom: 8 }}>
        <div>
          <h1 className="page-title">Eval cases</h1>
          <p className="page-subtitle">
            {items.length} case{items.length === 1 ? '' : 's'} · {train.length} train ·{' '}
            {test.length} test
          </p>
        </div>
        {!notFound && !apiError ? (
          <div className="page-actions" style={{ gap: 8 }}>
            <AddEvalCaseForm
              wsId={wsId}
              wfId={wfId}
              defaultTargetLabel={items[0]?.target_label_field || 'label'}
            />
            <GenerateEvalCasesButton wsId={wsId} wfId={wfId} hasExisting={items.length > 0} />
            {origin === 'copilot_studio' && items.length > 0 ? (
              <PushEvalCasesCopilotStudioButton wsId={wsId} wfId={wfId} />
            ) : null}
          </div>
        ) : null}
      </header>

      {apiError && (
        <div role="alert" className="api-banner" style={{ marginTop: 16 }}>
          <strong>{apiError.title}</strong> {apiError.detail}
        </div>
      )}

      {notFound && (
        <div role="alert" className="api-banner" style={{ marginTop: 16 }}>
          <strong>Workflow not found.</strong> No workflow with id <code>{wfId}</code>.
        </div>
      )}

      {!apiError && !notFound && items.length === 0 ? (
        <div
          style={{
            background: 'var(--bg)',
            border: '1px dashed var(--border)',
            borderRadius: 8,
            padding: 28,
            color: 'var(--text-muted)',
            fontSize: 13,
            lineHeight: 1.55,
            marginTop: 16,
          }}
        >
          <p style={{ margin: 0, marginBottom: 8 }}>
            <strong>No eval cases yet.</strong> Click <em>Generate eval cases</em> above
            to run the kernel&rsquo;s simulation-plan + eval-case generators against
            this workflow&rsquo;s spec. Takes ~30&ndash;45 seconds.
          </p>
          <p style={{ margin: 0, fontSize: 12 }}>
            Cases land as <code>provenance=nl-gen</code> rows in the{' '}
            <code>eval_cases</code> table and become the regression gate&rsquo;s
            initial test suite.
          </p>
        </div>
      ) : null}

      {items.length > 0 ? (
        <div className="eval-table" style={{ marginTop: 16 }}>
          <div className="eval-row head">
            <div>#</div>
            <div>Case</div>
            <div>Expected</div>
            <div>Fold</div>
            <div />
          </div>
          {items.map((c, i) => (
            <div key={c.id} className="eval-row">
              <div className="eval-num">{i + 1}</div>
              <div>
                <div className="eval-name">{c.case_id}</div>
                {c.rationale ? <div className="eval-source">{c.rationale}</div> : null}
                {c.target_label_field ? (
                  <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 2 }}>
                    target: <code>{c.target_label_field}</code> · seed {c.sim_seed} ·{' '}
                    step {c.target_step_index}/{c.n_steps} ·{' '}
                    provenance: <code>{c.provenance}</code>
                  </div>
                ) : null}
              </div>
              <div>
                <span
                  className={`pill ${
                    c.expected_value === true
                      ? 'green'
                      : c.expected_value === false
                        ? 'red'
                        : 'outline'
                  }`}
                >
                  {String(c.expected_value ?? '—')}
                </span>
              </div>
              <div>
                <span className={`pill ${c.is_test_fold ? 'amber' : 'outline'}`}>
                  {c.is_test_fold ? 'test' : 'train'}
                </span>
              </div>
              <DeleteEvalCaseButton wsId={wsId} wfId={wfId} caseId={c.id} />
            </div>
          ))}
        </div>
      ) : null}
    </>
  )
}
