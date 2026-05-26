// Server-only side-effect module: register the kernel auth-header provider on
// the client-safe `lib/api` module.
//
// `lib/api` is imported by client components, so it cannot itself pull in
// `next/headers` / Auth.js. Instead it exposes a registration hook; this
// module (imported once from the server-rendered root layout) wires the
// server-only `kernelAuthHeaders` into it. On the client the hook is never
// registered and kernel calls go out without an assertion (the kernel's
// dev fallback covers local dev).
import 'server-only'
import { registerKernelAuthProvider } from './api'
import { kernelAuthHeaders } from './kernel-principal'

registerKernelAuthProvider(kernelAuthHeaders)
