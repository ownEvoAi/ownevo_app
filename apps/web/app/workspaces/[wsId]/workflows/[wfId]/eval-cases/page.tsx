import {
  kernelError,
  KernelApiError,
  listWorkflowEvalCases,
  type EvalCaseSummary,
} from '@/lib/api'
import { GenerateEvalCasesButton } from './generate-button'

interface PageProps {
  params: Promise<{ wsId: string; wfId: string }>
}

export default async function WorkflowEvalCasesPage({ params }: PageProps) {
  const { wsId, wfId } = await params

  let items: EvalCaseSummary[] = []
  let apiError: { title: string; detail: string } | null = null
  let notFound = false
  try {
    const list = await listWorkflowEvalCases(wfId)
    items = list.items
  } catch (err) {
    if (err instanceof KernelApiError && err.status === 404) {
      notFound = true
    } else {
      apiError = kernelError(err)
    }
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
          <div className="page-actions">
            <GenerateEvalCasesButton wsId={wsId} wfId={wfId} hasExisting={items.length > 0} />
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
                    step {c.target_step_index}/{c.n_steps}
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
            </div>
          ))}
        </div>
      ) : null}
    </>
  )
}
