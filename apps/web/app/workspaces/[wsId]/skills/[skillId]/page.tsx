import Link from 'next/link'
import { notFound } from 'next/navigation'
import {
 getSkill,
 KernelApiError,
 type SkillDetail,
 type SkillRelatedEvalCase,
 type SkillVersionSummary,
} from '@/lib/api'
import { formatDateTime, relativeTime } from '@/lib/format'
import { SkillDiff } from '@/app/components/skill-diff'
import { DeployRollbackPanel } from './deploy-rollback'

interface PageProps {
 params: Promise<{ wsId: string; skillId: string }>
}

// (7.1.10 + 7.1.11) — per-skill detail.
//
// One route, two renderers: instruction skills (NL-gen-emitted) get
// the prompt variant (mock parity: 18-skill-detail.html); python /
// composite skills get the code variant with inline diff (mock
// parity: 18a-skill-detail-code.html). Branching happens at the
// page level so the URL contract stays one-skill-id-one-route.
export default async function SkillDetailPage({ params }: PageProps) {
 const { wsId, skillId } = await params

 let skill: SkillDetail
 try {
 skill = await getSkill(skillId)
 } catch (err) {
 if (err instanceof KernelApiError && err.status === 404) {
 notFound }
 throw err
 }

 return (
 <div>
 <SkillCrumbs wsId={wsId} skill={skill} />
 <SkillHeader skill={skill} />

 <div className="skill-grid">
 <div>
 {skill.kind === 'instruction' ? (
 <InstructionBody skill={skill} />
 ) : (
 <CodeBody skill={skill} />
 )}

 {skill.related_eval_cases.length > 0 ? (
 <RelatedEvalCases cases={skill.related_eval_cases} kind={skill.kind} />
 ) : (
 <RelatedEvalCasesEmpty kind={skill.kind} />
 )}
 </div>

 <aside>
 <DeployRollbackPanel
 wsId={wsId}
 skillId={skill.id}
 deployableProposalId={skill.deployable_proposal_id}
 deployableProposalVersionSeq={skill.deployable_proposal_version_seq}
 deployedProposalId={skill.deployed_proposal_id}
 deployedVersionSeq={skill.deployed_version_seq}
 />
 <RetentionCard skill={skill} />
 <VersionHistory versions={skill.versions} />
 </aside>
 </div>
 </div>
 )
}

function SkillCrumbs({ wsId, skill }: { wsId: string; skill: SkillDetail }) {
 return (
 <nav className="crumb-row">
 <Link href={`/workspaces/${wsId}`}>Workspace</Link>
 {skill.workflow_id && (
 <>
 <span className="sep">/</span>
 <Link href={`/workspaces/${wsId}/workflows/${skill.workflow_id}`}>
 {skill.workflow_description ?? skill.workflow_id}
 </Link>
 </>
 )}
 <span className="sep">/</span>
 <span>Skill {skill.id}</span>
 </nav>
 )
}

function SkillHeader({ skill }: { skill: SkillDetail }) {
 const kindPill =
 skill.kind === 'instruction'
 ? 'pill accent'
 : skill.kind === 'python'
 ? 'pill outline'
 : 'pill amber'
 return (
 <div className="prop-header">
 <div className="prop-pills">
 <span className={kindPill}>{skill.kind}</span>
 {skill.head_version_seq !== null && (
 <span className="pill outline" title="Best gate-validated version">
 Validated v{skill.head_version_seq}
 </span>
 )}
 {skill.deployed_version_seq !== null && (
 <span
 className="pill accent"
 title="Currently running in production"
 >
 Deployed v{skill.deployed_version_seq}
 </span>
 )}
 {skill.capability_tags.map((t) => (
 <span key={t} className="pill outline">
 {t}
 </span>
 ))}
 </div>
 <h1 className="prop-title">{skill.id}</h1>
 <div className="prop-meta-row">
 {skill.head_created_by && (
 <Meta label="Created by" value={skill.head_created_by} />
 )}
 {skill.head_created_at && (
 <Meta
 label="Last edited"
 value={`${relativeTime(skill.head_created_at)} · ${formatDateTime(skill.head_created_at)}`}
 />
 )}
 {skill.head_diff_summary && (
 <Meta label="Diff summary" value={skill.head_diff_summary} />
 )}
 </div>
 </div>
 )
}

function Meta({ label, value }: { label: string; value: string }) {
 return (
 <div className="prop-meta">
 <span className="prop-meta-label">{label}</span>
 <span className="prop-meta-value">{value}</span>
 </div>
 )
}

function InstructionBody({ skill }: { skill: SkillDetail }) {
 const content = skill.head_content ?? '(skill has no version yet)'
 return (
 <>
 <h2 className="section-title">SKILL.md</h2>
 <pre className="skill-content-pre">{content}</pre>
 </>
 )
}

function CodeBody({ skill }: { skill: SkillDetail }) {
 const head = skill.head_content
 if (head === null) {
 return (
 <>
 <h2 className="section-title">Skill code</h2>
 <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
 Skill has no version yet.
 </p>
 </>
 )
 }
 const signatures = extractSignatures(head)
 const diffLabel =
 skill.parent_version_seq !== null
 ? `Inline diff · v${skill.parent_version_seq} → v${skill.head_version_seq}`
 : 'Initial version'
 return (
 <>
 {signatures.length > 0 && (
 <>
 <h2 className="section-title">Function signatures</h2>
 <ul className="skill-signatures">
 {signatures.map((sig) => (
 <li key={sig}>
 <code>{sig}</code>
 </li>
 ))}
 </ul>
 </>
 )}

 <h2 className="section-title">{diffLabel}</h2>
 <SkillDiff
 current={skill.parent_content}
 proposed={head}
 parentVersionSeq={skill.parent_version_seq}
 />
 </>
 )
}

// Extract `def name(args):` and `class Name(...):` signatures from a
// Python source body. Regex-only — Pyodide / AST parse would be
// heavier than the cost of a few false positives in pathological
// strings. Mock parity: 18a-skill-detail-code.html § signatures list.
const MAX_SIGNATURES = 50
function extractSignatures(source: string): string[] {
 const out: string[] = []
 const re = /^[ \t]*(?:async\s+)?(def|class)\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)(\s*->\s*[^:]+)?:/gm
 let m: RegExpExecArray | null
 while ((m = re.exec(source)) !== null) {
 const kw = m[1]
 const name = m[2]
 const args = m[3].replace(/\s+/g, ' ').trim()
 const ret = (m[4] ?? '').trim()
 out.push(ret ? `${kw} ${name}(${args}) ${ret}` : `${kw} ${name}(${args})`)
 if (out.length > MAX_SIGNATURES) break
 }
 return out
}

function RetentionCard({ skill }: { skill: SkillDetail }) {
 const block = skill.head_retention_block
 if (skill.kind !== 'instruction' || !block || Object.keys(block).length === 0) {
 return null
 }
 const entries = Object.entries(block)
 return (
 <div className="sidebar-card">
 <div className="sidebar-title">Retention contract</div>
 <p
 style={{
 fontSize: 11.5,
 color: 'var(--text-muted)',
 marginBottom: 10,
 lineHeight: 1.4,
 }}
 >
 Parsed YAML frontmatter — see{' '}
 <code>SKILL_FORMAT.md</code>. The agent is required to acknowledge
 this contract on <code>skill_loaded</code>.
 </p>
 <dl className="retention-block">
 {entries.map(([k, v]) => (
 <div key={k} className="retention-row">
 <dt>{k}</dt>
 <dd>
 <code>{stringify(v)}</code>
 </dd>
 </div>
 ))}
 </dl>
 </div>
 )
}

function stringify(v: unknown): string {
 if (typeof v === 'string') return v
 try {
 return JSON.stringify(v)
 } catch {
 return String(v)
 }
}

function VersionHistory({ versions }: { versions: SkillVersionSummary[] }) {
 if (versions.length === 0) {
 return (
 <div className="sidebar-card">
 <div className="sidebar-title">Version history</div>
 <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
 No versions yet.
 </p>
 </div>
 )
 }
 return (
 <div className="sidebar-card">
 <div className="sidebar-title">Version history</div>
 <ol className="version-history">
 {versions.map((v) => (
 <li key={v.id} className="version-row">
 <div className="version-seq">v{v.version_seq}</div>
 <div className="version-summary">
 {v.diff_summary ?? '(no diff summary)'}
 </div>
 <div className="version-meta">
 <span>{v.created_by}</span>
 <span>·</span>
 <span title={formatDateTime(v.created_at)}>
 {relativeTime(v.created_at)}
 </span>
 </div>
 </li>
 ))}
 </ol>
 </div>
 )
}

function RelatedEvalCases({
 cases,
 kind,
}: {
 cases: SkillRelatedEvalCase[]
 kind: SkillDetail['kind']
}) {
 const heading =
 kind === 'instruction'
 ? `Retention-violation eval cases · ${cases.length}`
 : `Eval cases that moved · ${cases.length}`
 return (
 <>
 <h2 className="section-title">{heading}</h2>
 <table className="eval-case-table">
 <thead>
 <tr>
 <th>Case</th>
 <th>Provenance</th>
 <th>Fold</th>
 <th>Created</th>
 </tr>
 </thead>
 <tbody>
 {cases.map((c) => (
 <tr key={c.id}>
 <td>
 <code>{c.id.slice(0, 8)}</code>
 {c.expected_behavior && (
 <div
 style={{
 fontSize: 11.5,
 color: 'var(--text-muted)',
 marginTop: 2,
 }}
 >
 {summarizeExpected(c.expected_behavior)}
 </div>
 )}
 </td>
 <td>
 <span className="pill outline">{c.provenance}</span>
 </td>
 <td>{c.is_test_fold ? 'test' : 'train'}</td>
 <td>
 <span title={formatDateTime(c.created_at)}>
 {relativeTime(c.created_at)}
 </span>
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 </>
 )
}

function RelatedEvalCasesEmpty({ kind }: { kind: SkillDetail['kind'] }) {
 const message =
 kind === 'instruction'
 ? 'No retention-violation eval cases yet for this skill\'s workflow.'
 : 'No eval cases linked yet — proposals on this skill have not promoted any cluster cases.'
 return (
 <>
 <h2 className="section-title">Related eval cases</h2>
 <div
 style={{
 padding: 20,
 background: 'var(--bg)',
 border: '1px dashed var(--border)',
 borderRadius: 8,
 color: 'var(--text-muted)',
 fontSize: 13,
 }}
 >
 {message}
 </div>
 </>
 )
}

function summarizeExpected(expected: Record<string, unknown>): string {
 const note = expected['note']
 if (typeof note === 'string' && note.trim().length > 0) {
 return note.length > 90 ? `${note.slice(0, 90)}…` : note
 }
 const keys = Object.keys(expected)
 return keys.length > 0 ? `{${keys.slice(0, 3).join(', ')}}` : ''
}
