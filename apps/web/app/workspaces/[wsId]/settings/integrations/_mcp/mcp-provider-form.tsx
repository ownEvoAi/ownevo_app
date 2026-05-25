'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import type { McpOAuthClientView, McpProviderInfo, McpServer } from '@/lib/api'
import {
  deleteMcpClientAction,
  removeMcpServerAction,
  saveMcpClientAction,
  startMcpConnectAction,
  testMcpServerAction,
} from './actions'

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 10px',
  fontSize: 13,
  fontFamily: 'inherit',
  border: '1px solid var(--border)',
  borderRadius: 6,
  background: 'var(--bg)',
  color: 'var(--text)',
  marginTop: 4,
}

// One screen for connecting an MCP provider: register the OAuth app
// credentials, run the consent flow, and manage the resulting servers.
// Shared across the Slack / Google Workspace / Microsoft 365 pages — only
// the provider id + presets differ.
export function McpProviderForm({
  wsId,
  info,
  client,
  servers,
  banner,
  demoMode = false,
}: {
  wsId: string
  info: McpProviderInfo
  client: McpOAuthClientView
  servers: McpServer[]
  banner?: { kind: 'connected' | 'error'; detail: string } | null
  demoMode?: boolean
}) {
  const router = useRouter()
  const [isPending, startTransition] = useTransition()
  const [clientId, setClientId] = useState(client.client_id ?? '')
  const [clientSecret, setClientSecret] = useState('')
  const [tenant, setTenant] = useState(
    typeof client.config?.tenant === 'string' ? (client.config.tenant as string) : '',
  )
  const [serverName, setServerName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  function saveClient() {
    setError(null)
    setMsg(null)
    startTransition(async () => {
      const r = await saveMcpClientAction(wsId, info.provider, clientId, clientSecret, tenant)
      if (!r.ok) return setError(r.error)
      setClientSecret('')
      router.refresh()
    })
  }

  function removeClient() {
    setError(null)
    setMsg(null)
    startTransition(async () => {
      const r = await deleteMcpClientAction(wsId, info.provider)
      if (!r.ok) return setError(r.error)
      router.refresh()
    })
  }

  function connect() {
    setError(null)
    setMsg(null)
    startTransition(async () => {
      const r = await startMcpConnectAction(info.provider, serverName)
      if (!r.ok) return setError(r.error)
      // Hand the browser to the provider's consent screen. The kernel
      // callback redirects back here with ?connected=1 or ?error=.
      window.location.href = r.authorizeUrl
    })
  }

  function test(serverId: string) {
    setError(null)
    setMsg(null)
    startTransition(async () => {
      const r = await testMcpServerAction(wsId, info.provider, serverId)
      if (!r.ok) return setError(r.error)
      setMsg(
        r.status === 'ok'
          ? `Connection OK — ${r.toolCount ?? 0} tool(s) available.`
          : `Could not list tools${r.detail ? `: ${r.detail}` : ''}`,
      )
      router.refresh()
    })
  }

  function remove(serverId: string) {
    setError(null)
    setMsg(null)
    startTransition(async () => {
      const r = await removeMcpServerAction(wsId, info.provider, serverId)
      if (!r.ok) return setError(r.error)
      router.refresh()
    })
  }

  return (
    <div className="settings-stack">
      {banner && (
        <div
          role={banner.kind === 'error' ? 'alert' : undefined}
          className="api-banner"
          style={
            banner.kind === 'connected'
              ? { borderColor: 'var(--ok, #2e7d32)' }
              : undefined
          }
        >
          {banner.kind === 'connected'
            ? `Connected to ${info.display_name}.`
            : `Could not connect: ${banner.detail}`}
        </div>
      )}

      {/* 1. OAuth app credentials */}
      <div className="settings-card">
        <div className="settings-card-header">
          <h2 className="settings-card-title">OAuth app credentials</h2>
          <p className="settings-card-subtitle">
            From the OAuth app you registered with {info.display_name}. The
            client secret is encrypted at rest and never displayed again after
            saving. Set the redirect URI on that app to this kernel&apos;s{' '}
            <code>/api/mcp/oauth/{info.provider}/callback</code>.
          </p>
        </div>

        <p className="settings-label">
          Status:{' '}
          <strong>{client.configured ? 'configured' : 'not configured'}</strong>
        </p>

        <label className="settings-label" htmlFor="mcp-client-id">
          Client ID
        </label>
        <input
          id="mcp-client-id"
          type="text"
          value={clientId}
          onChange={(e) => setClientId(e.target.value)}
          disabled={isPending || demoMode}
          autoComplete="off"
          style={inputStyle}
        />

        <label className="settings-label" htmlFor="mcp-client-secret" style={{ marginTop: 10 }}>
          Client secret
        </label>
        <input
          id="mcp-client-secret"
          type="password"
          value={clientSecret}
          placeholder={client.configured ? '•••••• (set — enter to replace)' : ''}
          onChange={(e) => setClientSecret(e.target.value)}
          disabled={isPending || demoMode}
          autoComplete="off"
          style={inputStyle}
        />

        {info.tenant_scoped && (
          <>
            <label className="settings-label" htmlFor="mcp-tenant" style={{ marginTop: 10 }}>
              Tenant (optional — defaults to &quot;common&quot;)
            </label>
            <input
              id="mcp-tenant"
              type="text"
              value={tenant}
              onChange={(e) => setTenant(e.target.value)}
              disabled={isPending || demoMode}
              autoComplete="off"
              style={inputStyle}
            />
          </>
        )}

        <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
          <button
            type="button"
            onClick={saveClient}
            disabled={isPending || demoMode || !clientId.trim() || !clientSecret.trim()}
            className="btn btn-primary"
          >
            {isPending ? 'Saving…' : 'Save credentials'}
          </button>
          {client.configured && (
            <button
              type="button"
              onClick={removeClient}
              disabled={isPending || demoMode}
              className="btn btn-danger"
            >
              Remove
            </button>
          )}
        </div>
      </div>

      {/* 2. Connect a new server via OAuth */}
      <div className="settings-card">
        <div className="settings-card-header">
          <h2 className="settings-card-title">Connect {info.display_name}</h2>
          <p className="settings-card-subtitle">
            Authorize ownEvo to read from {info.display_name} via OAuth. Requested
            scopes: <code>{info.default_scopes.join(', ')}</code>.
          </p>
        </div>

        <label className="settings-label" htmlFor="mcp-server-name">
          Connection name
        </label>
        <input
          id="mcp-server-name"
          type="text"
          value={serverName}
          placeholder={`${info.provider}-prod`}
          onChange={(e) => setServerName(e.target.value)}
          disabled={isPending || demoMode || !client.configured}
          autoComplete="off"
          style={inputStyle}
        />

        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <button
            type="button"
            onClick={connect}
            disabled={isPending || demoMode || !client.configured || !serverName.trim()}
            className="btn btn-primary"
          >
            Connect with {info.display_name}
          </button>
        </div>
        {!client.configured && (
          <p style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 10 }}>
            Save the OAuth app credentials above first.
          </p>
        )}
      </div>

      {/* 3. Connected servers */}
      <div className="settings-card">
        <div className="settings-card-header">
          <h2 className="settings-card-title">Connected servers</h2>
        </div>
        {servers.length === 0 ? (
          <p style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
            No {info.display_name} servers connected yet.
          </p>
        ) : (
          <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            {servers.map((s) => (
              <li
                key={s.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 8,
                  padding: '8px 0',
                  borderTop: '1px solid var(--border)',
                  flexWrap: 'wrap',
                }}
              >
                <span style={{ fontSize: 13 }}>
                  <strong>{s.name}</strong>{' '}
                  <span style={{ color: 'var(--text-muted)' }}>
                    · {s.auth_kind}
                    {s.validation_status ? ` · last test: ${s.validation_status}` : ''}
                  </span>
                </span>
                <span style={{ display: 'flex', gap: 8 }}>
                  <button
                    type="button"
                    onClick={() => test(s.id)}
                    disabled={isPending || demoMode}
                    className="btn"
                  >
                    Test
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(s.id)}
                    disabled={isPending || demoMode}
                    className="btn btn-danger"
                  >
                    Remove
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
        {msg && (
          <p style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 10 }}>{msg}</p>
        )}
      </div>

      {error && (
        <p role="alert" style={{ fontSize: 12.5, color: 'var(--danger, #c0392b)' }}>
          {error}
        </p>
      )}
    </div>
  )
}
