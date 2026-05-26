// Next.js instrumentation hook — runs once at server startup.
// https://nextjs.org/docs/app/building-your-application/optimizing/instrumentation
//
// Boot-time guards: two mutually exclusive flag combinations are caught here
// so a misconfigured deployment fails at startup rather than at first user
// interaction.
//
// 1. OWNEVO_DEV_AUTH=true + OWNEVO_INTERNAL_AUTH_KEY — dev-auth bypasses
//    workspace isolation in any deployment that uses the signing key. Mirror
//    the kernel's RuntimeError so both processes refuse to start together.
//
// 2. OWNEVO_DEV_AUTH=true + AUTH_GOOGLE_ID/SECRET — the dev-auth path only
//    accepts the credentials/dev provider and carries no signing key, so any
//    Google sign-in attempt will 500 immediately. Crash at startup, not at
//    user interaction.
export async function register() {
  const devAuth = process.env.OWNEVO_DEV_AUTH?.toLowerCase() === 'true'
  const hasKey = Boolean(process.env.OWNEVO_INTERNAL_AUTH_KEY)
  const hasGoogle = Boolean(process.env.AUTH_GOOGLE_ID && process.env.AUTH_GOOGLE_SECRET)

  if (devAuth && hasKey) {
    throw new Error(
      'OWNEVO_DEV_AUTH=true is set alongside OWNEVO_INTERNAL_AUTH_KEY. ' +
      'These flags are mutually exclusive: dev-auth bypasses workspace isolation ' +
      'in any deployment that uses the shared signing key. ' +
      'Unset OWNEVO_DEV_AUTH in production.',
    )
  }

  if (devAuth && hasGoogle) {
    throw new Error(
      'OWNEVO_DEV_AUTH=true is set alongside AUTH_GOOGLE_ID/AUTH_GOOGLE_SECRET. ' +
      'These flags are mutually exclusive: Google sign-ins will 500 immediately ' +
      'because the dev-auth path only accepts the credentials/dev provider and ' +
      'carries no signing key. ' +
      'Unset OWNEVO_DEV_AUTH or remove AUTH_GOOGLE_ID/AUTH_GOOGLE_SECRET.',
    )
  }
}
