// Tests for the production boot guards in instrumentation.ts.
// Run with: node --experimental-strip-types --test instrumentation.test.ts
//
// register() reads process.env at call time so env manipulation between tests
// is sufficient — no module reload needed.
import { after, afterEach, before, describe, it } from 'node:test'
import assert from 'node:assert/strict'

type EnvSnapshot = Record<string, string | undefined>

function captureEnv(keys: string[]): EnvSnapshot {
  return Object.fromEntries(keys.map((k) => [k, process.env[k]]))
}

function applyEnv(vars: Record<string, string | undefined>) {
  for (const [k, v] of Object.entries(vars)) {
    if (v === undefined) {
      delete process.env[k]
    } else {
      process.env[k] = v
    }
  }
}

const KEYS = [
  'NODE_ENV',
  'OWNEVO_DEV_AUTH',
  'OWNEVO_INTERNAL_AUTH_KEY',
  'AUTH_GOOGLE_ID',
  'AUTH_GOOGLE_SECRET',
]

let register: () => Promise<void>
let snapshot: EnvSnapshot

before(async () => {
  ;({ register } = await import('./instrumentation.ts'))
  snapshot = captureEnv(KEYS)
})

afterEach(() => {
  applyEnv(snapshot)
})

after(() => {
  applyEnv(snapshot)
})

describe('isProduction block', () => {
  it('throws when NODE_ENV=production and OWNEVO_DEV_AUTH=true', async () => {
    applyEnv({
      NODE_ENV: 'production',
      OWNEVO_DEV_AUTH: 'true',
      OWNEVO_INTERNAL_AUTH_KEY: undefined,
      AUTH_GOOGLE_ID: undefined,
      AUTH_GOOGLE_SECRET: undefined,
    })
    await assert.rejects(register, /OWNEVO_DEV_AUTH/)
  })

  it('throws when NODE_ENV=production and OWNEVO_INTERNAL_AUTH_KEY is absent', async () => {
    applyEnv({
      NODE_ENV: 'production',
      OWNEVO_DEV_AUTH: undefined,
      OWNEVO_INTERNAL_AUTH_KEY: undefined,
      AUTH_GOOGLE_ID: undefined,
      AUTH_GOOGLE_SECRET: undefined,
    })
    await assert.rejects(register, /OWNEVO_INTERNAL_AUTH_KEY/)
  })

  it('throws when NODE_ENV=production and OWNEVO_INTERNAL_AUTH_KEY is whitespace-only', async () => {
    applyEnv({
      NODE_ENV: 'production',
      OWNEVO_DEV_AUTH: undefined,
      OWNEVO_INTERNAL_AUTH_KEY: '   ',
      AUTH_GOOGLE_ID: undefined,
      AUTH_GOOGLE_SECRET: undefined,
    })
    await assert.rejects(register, /OWNEVO_INTERNAL_AUTH_KEY/)
  })

  it('does not throw when NODE_ENV=production, dev-auth off, and key is set', async () => {
    applyEnv({
      NODE_ENV: 'production',
      OWNEVO_DEV_AUTH: undefined,
      OWNEVO_INTERNAL_AUTH_KEY: 'a-valid-signing-key',
      AUTH_GOOGLE_ID: undefined,
      AUTH_GOOGLE_SECRET: undefined,
    })
    await assert.doesNotReject(register)
  })

  it('does not activate when NODE_ENV is not production', async () => {
    applyEnv({
      NODE_ENV: 'development',
      OWNEVO_DEV_AUTH: undefined,
      OWNEVO_INTERNAL_AUTH_KEY: undefined,
      AUTH_GOOGLE_ID: undefined,
      AUTH_GOOGLE_SECRET: undefined,
    })
    // The earlier guards (devAuth+hasKey, devAuth+hasGoogle) are both off, so
    // register() completes without error even though the key is absent.
    await assert.doesNotReject(register)
  })
})
