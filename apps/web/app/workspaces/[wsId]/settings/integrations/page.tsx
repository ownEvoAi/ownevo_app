import Link from 'next/link'

interface PageProps {
 params: Promise<{ wsId: string }>
}

const INTEGRATIONS = [
 {
 slug: 'slack',
 name: 'Slack',
 blurb: 'Read channels and messages as an agent data source (MCP, OAuth).',
 },
 {
 slug: 'google-workspace',
 name: 'Google Workspace',
 blurb: 'Drive, Docs, Sheets, and Gmail as agent data sources (MCP, OAuth).',
 },
 {
 slug: 'microsoft-365',
 name: 'Microsoft 365',
 blurb: 'OneDrive, Word, Excel, and Outlook as agent data sources (MCP, OAuth).',
 },
 {
 slug: 'upload',
 name: 'File upload',
 blurb: 'Upload CSV / Excel / Parquet spreadsheets and PDF / DOCX documents as data sources.',
 },
 {
 slug: 'langsmith',
 name: 'LangSmith',
 blurb: 'Ship approved fixes back to a LangSmith workspace as prompt versions.',
 },
]

// Settings → Integrations index. Links to each provider's connect page.
export default async function IntegrationsIndexPage({ params }: PageProps) {
 const { wsId } = await params
 const base = `/workspaces/${wsId}/settings/integrations`
 return (
 <>
 <h1 className="page-title">Integrations</h1>
 <div className="settings-stack">
 {INTEGRATIONS.map((it) => (
 <Link
 key={it.slug}
 href={`${base}/${it.slug}`}
 className="settings-card"
 style={{ display: 'block', textDecoration: 'none', color: 'inherit' }}
 >
 <div className="settings-card-header">
 <h2 className="settings-card-title">{it.name}</h2>
 <p className="settings-card-subtitle">{it.blurb}</p>
 </div>
 </Link>
 ))}
 </div>
 </>
 )
}
