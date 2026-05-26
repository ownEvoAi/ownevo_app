import { BrandMark } from './BrandMark'

export function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="setup-shell">
      <div className="setup-card">
        <div className="setup-brand">
          <BrandMark />
          <span className="setup-brand-name">
            <span className="logo-own">own</span>
            <span className="logo-evo">Evo</span>
          </span>
        </div>
        {children}
      </div>
    </div>
  )
}
