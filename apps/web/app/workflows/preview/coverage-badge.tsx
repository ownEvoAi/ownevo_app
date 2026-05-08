// W5.5 — meta-eval coverage badge.
//
// The headliner of the "Review what we'll build" page. Renders the
// MetaEvalJudgment (PR #54) as a single card answering: "is this
// generated bundle ready for the agent loop?" and "where's the gap
// if not?". Three per-dimension rows, each pass/partial/fail with the
// judge's one-line rationale, plus an overall verdict + aggregate
// score so a non-engineer reviewer can decide in <30 seconds.
//
// Pure server component, zero client JS. Verdicts are categorical
// (pass/partial/fail) so we render them as colored pills + glyphs;
// the aggregate score follows the per-dimension mapping
// (pass=1.0, partial=0.5, fail=0.0) so it matches what the kernel's
// MetaEvalJudgment.aggregate_score() returns.

import type { MetaEvalJudgment } from '@/lib/api'

const DIMENSION_SCORE: Record<'pass' | 'partial' | 'fail', number> = {
  pass: 1.0,
  partial: 0.5,
  fail: 0.0,
}

function aggregateScore(j: MetaEvalJudgment): number {
  return (
    (DIMENSION_SCORE[j.sim_coverage.verdict] +
      DIMENSION_SCORE[j.eval_case_coverage.verdict] +
      DIMENSION_SCORE[j.metric_alignment.verdict]) /
    3.0
  )
}

const DIMENSION_LABELS = {
  sim_coverage: 'Sim coverage',
  eval_case_coverage: 'Eval cases',
  metric_alignment: 'Metric alignment',
} as const

const DIMENSION_HELPER = {
  sim_coverage: 'Does the simulator instantiate every entity from your description?',
  eval_case_coverage: 'Do the eval cases exercise the past-misses you mentioned?',
  metric_alignment: 'Does the metric direction match the past-miss framing?',
} as const

const VERDICT_TONE: Record<'pass' | 'partial' | 'fail', 'green' | 'amber' | 'red'> = {
  pass: 'green',
  partial: 'amber',
  fail: 'red',
}

export function MetaEvalCoverageBadge({
  judgment,
}: {
  judgment: MetaEvalJudgment
}) {
  const score = aggregateScore(judgment)
  const isGood = judgment.overall_verdict === 'good'

  return (
    <div className={`coverage-badge ${isGood ? 'good' : 'bad'}`}>
      <div className="coverage-head">
        <div className={`coverage-icon ${VERDICT_TONE[overallTone(judgment)]}`}>
          {isGood ? <CheckGlyph /> : <AlertGlyph />}
        </div>
        <div className="coverage-headline">
          <div className="coverage-title">
            {isGood
              ? 'Ready for the agent loop'
              : 'Held back from the agent loop'}
            <span className="coverage-score">{Math.round(score * 100)}% coverage</span>
          </div>
          <p className="coverage-sub">{judgment.overall_rationale}</p>
        </div>
      </div>

      <div className="coverage-dims">
        {(
          ['sim_coverage', 'eval_case_coverage', 'metric_alignment'] as const
        ).map((key) => {
          const dim = judgment[key]
          return (
            <div key={key} className={`coverage-dim ${dim.verdict}`}>
              <div className="coverage-dim-head">
                <span className="coverage-dim-label">{DIMENSION_LABELS[key]}</span>
                <span className={`pill ${VERDICT_TONE[dim.verdict]}`}>
                  {dim.verdict}
                </span>
              </div>
              <p className="coverage-dim-helper">{DIMENSION_HELPER[key]}</p>
              <p className="coverage-dim-rationale">{dim.rationale}</p>
            </div>
          )
        })}
      </div>

      <div className="coverage-footer">
        <span className="coverage-footer-label">W5.5 meta-eval gate</span>
        <span className="coverage-footer-meta">
          aggregate {(score).toFixed(2)} · pass=1.0 · partial=0.5 · fail=0.0
        </span>
      </div>
    </div>
  )
}

// `overallTone` follows the binary verdict — the per-dimension mix can
// surface yellow when overall is still "good" (judge calls partial-mix
// good), so we don't derive tone from the dimensions.
function overallTone(j: MetaEvalJudgment): 'pass' | 'partial' | 'fail' {
  if (j.overall_verdict === 'bad') return 'fail'
  // overall = good — color green even if one dimension is partial.
  return 'pass'
}

function CheckGlyph() {
  return (
    <svg viewBox="0 0 18 18" aria-hidden>
      <path
        d="M3 9 L7.5 13 L15 5"
        stroke="currentColor"
        strokeWidth={2.5}
        fill="none"
      />
    </svg>
  )
}

function AlertGlyph() {
  return (
    <svg viewBox="0 0 18 18" aria-hidden>
      <path
        d="M9 3 L9 11 M9 13.5 L9 14.5"
        stroke="currentColor"
        strokeWidth={2.5}
        fill="none"
      />
    </svg>
  )
}
