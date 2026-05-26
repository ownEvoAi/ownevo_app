'use server'

import { revalidatePath } from 'next/cache'
import { deleteDataUpload, KernelApiError, uploadDataFile } from '@/lib/api'

type ActionResult = { ok: true } | { ok: false; error: string }

function errorMessage(err: unknown): string {
 if (err instanceof KernelApiError) return err.detail
 return err instanceof Error ? err.message : 'Unknown error'
}

function uploadPath(wsId: string): string {
 return `/workspaces/${wsId}/settings/integrations/upload`
}

export async function uploadFileAction(
 wsId: string,
 formData: FormData,
): Promise<ActionResult> {
 const file = formData.get('file')
 if (!(file instanceof File) || file.size === 0) {
 return { ok: false, error: 'Choose a file to upload.' }
 }
 try {
 await uploadDataFile(file)
 } catch (err) {
 return { ok: false, error: errorMessage(err) }
 }
 revalidatePath(uploadPath(wsId))
 return { ok: true }
}

export async function deleteUploadAction(
 wsId: string,
 uploadId: string,
): Promise<ActionResult> {
 try {
 await deleteDataUpload(uploadId)
 } catch (err) {
 return { ok: false, error: errorMessage(err) }
 }
 revalidatePath(uploadPath(wsId))
 return { ok: true }
}
