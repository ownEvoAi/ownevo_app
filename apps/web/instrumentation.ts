// Next.js instrumentation hook — runs once at server startup.
// https://nextjs.org/docs/app/building-your-application/optimizing/instrumentation
//
// Boot-time guards: dangerous environment-flag combinations are caught here
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
//
// 3. NODE_ENV=production with dev-auth still on, or with required secrets
//    missing. The first two guards catch the most dangerous combinations
//    on their own; this one closes the remaining holes by refusing any
//    prod boot that depends on a dev-only fallback or omits a required
//    secret.
export async function register() {
  const devAuth = process.env.OWNEVO_DEV_AUTH?.toLowerCase() === 'true'
  const hasKey = Boolean(process.env.OWNEVO_INTERNAL_AUTH_KEY)
  const hasGoogle = Boolean(process.env.AUTH_GOOGLE_ID && process.env.AUTH_GOOGLE_SECRET)
  const isProduction = process.env.NODE_ENV === 'production'

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

  if (isProduction) {
    if (devAuth) {
      throw new Error(
        'NODE_ENV=production is set with OWNEVO_DEV_AUTH=true. ' +
        'The dev-auth fallback resolves every unauthenticated request to ' +
        'the seeded dev user and the default workspace, which would bypass ' +
        'real authentication in production. Unset OWNEVO_DEV_AUTH.',
      )
    }
    if (!hasKey) {
      throw new Error(
        'NODE_ENV=production but OWNEVO_INTERNAL_AUTH_KEY is not set. ' +
        'The web app must share this signing key with the kernel to mint ' +
        'identity assertions; without it every authenticated request to ' +
        'the kernel will be rejected.',
      )
    }
  }
}
