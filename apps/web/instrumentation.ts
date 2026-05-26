// Next.js instrumentation hook — runs once at server startup.
// https://nextjs.org/docs/app/building-your-application/optimizing/instrumentation
//
// Boot-time guard: OWNEVO_DEV_AUTH=true and OWNEVO_INTERNAL_AUTH_KEY set
// together are mutually exclusive on the kernel side (the kernel refuses to
// start with both flags). Mirror that check here so a misconfigured
// production deployment is caught at the web-process level too, not just at
// kernel startup.
//
// Secondary guard: OWNEVO_DEV_AUTH=true alongside any real OAuth credential
// (AUTH_GOOGLE_ID) is caught at sign-in time by the jwt callback (it throws
// for non-dev providers with no key). Log an explicit warning here so the
// misconfiguration is visible at startup rather than only at the first sign-in.
export async function register() {
 const devAuth = process.env.OWNEVO_DEV_AUTH?.toLowerCase() === 'true'
 const hasKey = Boolean(process.env.OWNEVO_INTERNAL_AUTH_KEY)
 const hasGoogle = Boolean(process.env.AUTH_GOOGLE_ID)

 if (devAuth && hasKey) {
  // Mirror the kernel's RuntimeError — fail loudly rather than silently
  // resolving every real user to the seeded dev principal.
  throw new Error(
   'OWNEVO_DEV_AUTH=true is set alongside OWNEVO_INTERNAL_AUTH_KEY. ' +
   'These flags are mutually exclusive: dev-auth bypasses workspace isolation ' +
   'in any deployment that uses the shared signing key. ' +
   'Unset OWNEVO_DEV_AUTH in production.',
  )
 }

 if (devAuth && hasGoogle) {
  // Not immediately fatal — the jwt callback guards the sign-in path — but
  // log a warning so the misconfiguration is visible before a user hits it.
  console.warn(
   '[ownEvo] OWNEVO_DEV_AUTH=true is set alongside AUTH_GOOGLE_ID. ' +
   'Google sign-ins will fail (the dev-auth fallback only applies to the ' +
   'credentials/dev provider). Set OWNEVO_INTERNAL_AUTH_KEY or remove AUTH_GOOGLE_ID.',
  )
 }
}
