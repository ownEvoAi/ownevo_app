import { type DataUpload, kernelError, listDataUploads } from '@/lib/api'
import { isDemoMode } from '@/lib/demo-mode'
import { UploadForm } from './upload-form'

interface PageProps {
  params: Promise<{ wsId: string }>
}

// Settings → Integrations → Upload. Spreadsheet + document uploads as agent
// data sources (Track 17.0.3 / 17.0.4).
export default async function UploadIntegrationPage({ params }: PageProps) {
  const { wsId } = await params
  const demoMode = isDemoMode()

  let uploads: DataUpload[] = []
  let apiError: { title: string; detail: string } | null = null
  try {
    uploads = await listDataUploads()
  } catch (err) {
    apiError = kernelError(err)
  }

  return (
    <>
      <h1 className="page-title">Integrations · Upload</h1>
      {apiError && (
        <div role="alert" className="api-banner">
          <strong>{apiError.title}</strong> {apiError.detail}
        </div>
      )}
      {!apiError && <UploadForm wsId={wsId} uploads={uploads} demoMode={demoMode} />}
    </>
  )
}
