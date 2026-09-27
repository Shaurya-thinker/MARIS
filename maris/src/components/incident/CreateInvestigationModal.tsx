import { useEffect } from 'react'
import { CheckCircle2, Compass, Waves, X } from 'lucide-react'

interface CreateInvestigationModalProps {
  isOpen: boolean
  onClose: () => void
  onSubmit?: (payload: any) => Promise<void>
  onNavigateToEvaluator?: (opts?: { mode?: 'real' | 'evaluator' | 'synthetic'; runId?: string; investigationId?: string }) => void
}

export function CreateInvestigationModal({ isOpen, onClose, onNavigateToEvaluator }: CreateInvestigationModalProps) {
  // Lock body scroll while modal is active
  useEffect(() => {
    if (!isOpen) return
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)

    return () => {
      document.body.style.overflow = originalOverflow || ''
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [isOpen, onClose])

  if (!isOpen) return null

  function handleLaunchWizard() {
    onClose()
    if (onNavigateToEvaluator) {
      onNavigateToEvaluator({ mode: 'real' })
    } else if (typeof window !== 'undefined') {
      window.location.hash = '#evaluator'
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    }
  }

  function handleBackdropClick(e: React.MouseEvent<HTMLDivElement>) {
    if (e.target === e.currentTarget) {
      onClose()
    }
  }

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
      onClick={handleBackdropClick}
    >
      <div className="modal-card" style={{ maxWidth: '640px' }}>
        {/* Header */}
        <div className="modal-header">
          <div>
            <span className="section-kicker">Mission Initialization</span>
            <h2 id="modal-title" className="modal-title">
              Initialize Spill Investigation
            </h2>
            <p className="modal-subtitle">
              Launch the authoritative real-data SAR attribution pipeline for satellite scenes, metocean physics, and AIS vessel attribution.
            </p>
          </div>
          <button
            className="icon-button modal-close-btn"
            type="button"
            onClick={onClose}
            aria-label="Close modal"
          >
            <X size={16} />
          </button>
        </div>

        <div className="modal-form" style={{ gap: '1.25rem' }}>
          {/* Authoritative Pipeline Card */}
          <div
            style={{
              padding: '1.25rem',
              background: 'linear-gradient(145deg, rgba(56, 189, 248, 0.08) 0%, rgba(15, 23, 42, 0.6) 100%)',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid rgba(56, 189, 248, 0.25)',
              display: 'flex',
              flexDirection: 'column',
              gap: '1rem',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.5rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span className="re-badge" style={{ fontSize: '0.7rem', padding: '0.2rem 0.5rem', background: 'rgba(56, 189, 248, 0.15)', color: '#38bdf8', borderColor: 'rgba(56, 189, 248, 0.3)' }}>
                  AUTHORITATIVE REAL-DATA ENGINE
                </span>
              </div>
              <span style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)', fontFamily: 'var(--font-mono)' }}>
                6-STEP PIPELINE
              </span>
            </div>

            <div>
              <h3 style={{ fontSize: '1.05rem', fontWeight: 600, color: '#fff', margin: '0 0 0.4rem 0', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <Compass size={18} style={{ color: 'var(--color-accent)' }} />
                Real-Data Observation Wizard
              </h3>
              <p style={{ fontSize: '0.84rem', color: 'var(--color-text-subtle)', lineHeight: 1.55, margin: 0 }}>
                Executes the authentic MARIS attribution workflow: select verified Copernicus Sentinel-1 SAR imagery (including Corsica 2018 benchmark or live Copernicus CDSE discovery), query ERA5 atmospheric wind &amp; CMEMS hydrodynamic currents, compute backward drift physics trajectories, filter candidates against authoritative transponder database (<code>ais_vessels.db</code>), and generate court-admissible scientific PDF dossiers.
              </p>
            </div>

            {/* Feature Highlights Grid */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(2, 1fr)',
                gap: '0.6rem',
                padding: '0.75rem',
                background: 'rgba(15, 23, 42, 0.5)',
                borderRadius: 'var(--radius-xs)',
                border: '1px solid rgba(255, 255, 255, 0.06)',
                fontSize: '0.78rem',
                color: 'var(--color-text-secondary)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                <CheckCircle2 size={13} style={{ color: '#4ade80', flexShrink: 0 }} />
                <span>Sentinel-1 SAR Scenes</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                <CheckCircle2 size={13} style={{ color: '#4ade80', flexShrink: 0 }} />
                <span>ERA5 / CMEMS Metocean</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                <CheckCircle2 size={13} style={{ color: '#4ade80', flexShrink: 0 }} />
                <span>Lagrangian Drift Physics</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                <CheckCircle2 size={13} style={{ color: '#4ade80', flexShrink: 0 }} />
                <span>ais_vessels.db Matching</span>
              </div>
            </div>
          </div>

          {/* Footer Actions */}
          <div className="modal-actions" style={{ marginTop: '0.5rem' }}>
            <button
              className="modal-btn-secondary"
              type="button"
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              className="modal-btn-primary"
              type="button"
              onClick={handleLaunchWizard}
              style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem' }}
            >
              <Compass size={15} />
              Take to Real-Data Observation Wizard →
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
