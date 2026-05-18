/**
 * Vertical-template starters for /workflows/new.
 *
 * Three buyer-persona one-click starters. Picking a card prefills the
 * description textarea and tags the workflow with `template_id` — the
 * kernel persists that as `workflows.created_from_template` for analytics.
 *
 * The design-agent discovery questions per template live kernel-side in
 * `apps/kernel/src/ownevo_kernel/design_agent/prompts/`. The chat panel
 * on `/workflows/new/design` fetches them via `POST /api/design-agent/
 * next-question` keyed on the template `id`, so the per-template prompt
 * set has a single source of truth.
 */
export interface VerticalTemplate {
  id: string
  name: string
  /** One-sentence persona + verb the card surfaces above the description. */
  tagline: string
  /** Buyer persona — the decider who owns the workflow today. */
  persona: string
  /** Pre-filled textarea content. Mirrors the 5-minute demo plan. */
  sample_description: string
  /** Suggested tools / capabilities the agent would need. */
  suggested_tools: string[]
  /** Suggested personas the simulator would model. */
  suggested_personas: string[]
}

export const VERTICAL_TEMPLATES: VerticalTemplate[] = [
  {
    id: 'retail-demand-planning',
    name: 'Retail demand planning',
    tagline: 'Forecast SKU-store demand, flag markdown-risk SKUs',
    persona: 'VP supply chain / category planner',
    sample_description:
      'Forecast weekly demand at SKU-store level for the next four weeks. ' +
      'Flag SKUs likely to need markdown within four weeks. Account for ' +
      'seasonality, promotions, and regional variance. The category planner ' +
      'reviews flags weekly and decides which to action. Past misses: we ' +
      'underforecast Q4 promo lift on cold-weather SKUs in the Northeast, ' +
      'and overforecast holiday demand on slow-moving SKUs that ended up ' +
      'on markdown by week six.',
    suggested_tools: [
      'load_sales_history',
      'load_promo_calendar',
      'run_forecast',
      'flag_markdown_risk',
    ],
    suggested_personas: ['category-planner', 'store-manager'],
  },
  {
    id: 'credit-risk-recalibration',
    name: 'Credit risk recalibration',
    tagline: 'Recalibrate PD models monthly against fresh portfolio data',
    persona: 'Chief risk officer / credit modeling lead',
    sample_description:
      'Recalibrate probability-of-default models monthly using new ' +
      'portfolio performance data. Detect drift in PD predictions versus ' +
      'realized defaults across segment, vintage, and macro factor. Propose ' +
      'adjusted PD weights when drift exceeds tolerance, with a written ' +
      'rationale the CRO reviews before sign-off. Past misses: we missed ' +
      'a hospitality-sector concentration shift through spring 2024, and ' +
      'held PD too low on a vintage with rising DPD in Q3.',
    suggested_tools: [
      'load_portfolio_snapshot',
      'compute_realized_default_rate',
      'detect_drift',
      'propose_pd_adjustment',
    ],
    suggested_personas: ['credit-modeler', 'cro-reviewer'],
  },
  {
    id: 'clinical-trial-site-selection',
    name: 'Clinical trial site selection',
    tagline: 'Score and rank candidate trial sites against protocol criteria',
    persona: 'Chief medical officer / clinical operations lead',
    sample_description:
      'Score and rank clinical trial sites for a new Phase III oncology ' +
      'study. Factor recruitment speed, patient demographic diversity, ' +
      'investigator track record, and operational readiness. Surface a ' +
      'shortlist of 12 sites with per-site rationale the clinical ops ' +
      'lead reviews before contracting. Past misses: we under-recruited ' +
      'rare-mutation arms when we picked sites optimized for raw enrollment ' +
      'speed, and shortlisted two sites that failed audit readiness checks ' +
      'late in the activation pipeline.',
    suggested_tools: [
      'load_site_history',
      'score_recruitment_speed',
      'score_diversity_reach',
      'score_audit_readiness',
      'rank_shortlist',
    ],
    suggested_personas: ['clinical-ops-lead', 'site-investigator'],
  },
]

export function getTemplate(id: string): VerticalTemplate | undefined {
  return VERTICAL_TEMPLATES.find((t) => t.id === id)
}
