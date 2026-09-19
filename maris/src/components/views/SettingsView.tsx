import { useState } from 'react'
import { Check, ChevronDown, ChevronUp, Compass, Cpu, Database, Eye, Globe, Radio, Server, Sliders } from 'lucide-react'

interface SettingsViewProps {
  isBackendUnavailable?: boolean
  onNavigateToView: (view: 'workspace') => void
}

export function SettingsView({ isBackendUnavailable = false, onNavigateToView }: SettingsViewProps) {
  const [coordFormat, setCoordFormat] = useState<'DD' | 'DDM' | 'DMS'>('DD')
  const [pollingRate, setPollingRate] = useState<'5s' | '10s' | '30s' | 'manual'>('5s')
  const [defaultLayers, setDefaultLayers] = useState({ spill: true, drift: true, vessels: true })
  const [saveSuccess, setSaveSuccess] = useState(false)

  // Collapsible section states
  const [sections, setSections] = useState({
    display: true,
    telemetry: true,
    mapLayers: true,
    diagnostics: true,
  })

  function toggleSection(key: keyof typeof sections) {
    setSections((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  function handleSave() {
    setSaveSuccess(true)
    setTimeout(() => setSaveSuccess(false), 2500)
  }

  return (
    <div className="view-container">
      {/* Editorial Header */}
      <div className="view-hero">
        <div className="view-hero-text">
          <span className="view-kicker">Configuration</span>
          <h1 className="view-headline">Mission Console Settings</h1>
          <p className="view-lead">
            Coordinate display formats, telemetry polling intervals, default GIS map layers, and system diagnostics.
          </p>
        </div>
        <button className="secondary-button" type="button" onClick={() => onNavigateToView('workspace')}>
          <Compass size={14} /> Back to GIS Workspace
        </button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem', maxWidth: '820px' }}>
        {/* Section 1: Display */}
        <section
          style={{
            borderRadius: 'var(--radius-md)',
            background: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              padding: '1.25rem',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              cursor: 'pointer',
              background: 'rgba(8, 23, 27, 0.4)',
            }}
            onClick={() => toggleSection('display')}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
              <Globe size={16} color="var(--color-accent)" />
              <h3 style={{ fontSize: '1.05rem', fontWeight: 700, margin: 0, color: '#fff' }}>Display &amp; Coordinates</h3>
            </div>
            {sections.display ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </div>

          {sections.display && (
            <div style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div>
                <label style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)', display: 'block', marginBottom: '0.4rem' }}>
                  Coordinate Notation
                </label>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  {(['DD', 'DDM', 'DMS'] as const).map((fmt) => (
                    <button
                      key={fmt}
                      type="button"
                      className={coordFormat === fmt ? 'primary-button' : 'secondary-button'}
                      style={{ padding: '0.4rem 0.85rem', fontSize: '0.75rem' }}
                      onClick={() => setCoordFormat(fmt)}
                    >
                      {fmt === 'DD' ? 'Decimal Degrees (DD)' : fmt === 'DDM' ? 'Degrees Decimal Min (DDM)' : 'DMS (Historical)'}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
        </section>

        {/* Section 2: Telemetry */}
        <section
          style={{
            borderRadius: 'var(--radius-md)',
            background: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              padding: '1.25rem',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              cursor: 'pointer',
              background: 'rgba(8, 23, 27, 0.4)',
            }}
            onClick={() => toggleSection('telemetry')}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
              <Radio size={16} color="var(--color-info)" />
              <h3 style={{ fontSize: '1.05rem', fontWeight: 700, margin: 0, color: '#fff' }}>Telemetry &amp; Polling</h3>
            </div>
            {sections.telemetry ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </div>

          {sections.telemetry && (
            <div style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div>
                <label style={{ fontSize: '0.75rem', color: 'var(--color-text-muted)', display: 'block', marginBottom: '0.4rem' }}>
                  Live Telemetry Refresh Rate
                </label>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  {(['5s', '10s', '30s', 'manual'] as const).map((rate) => (
                    <button
                      key={rate}
                      type="button"
                      className={pollingRate === rate ? 'primary-button' : 'secondary-button'}
                      style={{ padding: '0.4rem 0.85rem', fontSize: '0.75rem' }}
                      onClick={() => setPollingRate(rate)}
                    >
                      {rate === '5s' ? '5 Seconds (High Priority)' : rate === '10s' ? '10 Seconds' : rate === '30s' ? '30 Seconds' : 'Manual Refresh Only'}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
        </section>

        {/* Section 3: Map Layers */}
        <section
          style={{
            borderRadius: 'var(--radius-md)',
            background: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              padding: '1.25rem',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              cursor: 'pointer',
              background: 'rgba(8, 23, 27, 0.4)',
            }}
            onClick={() => toggleSection('mapLayers')}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
              <Eye size={16} color="var(--color-processing)" />
              <h3 style={{ fontSize: '1.05rem', fontWeight: 700, margin: 0, color: '#fff' }}>Map Layers</h3>
            </div>
            {sections.mapLayers ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </div>

          {sections.mapLayers && (
            <div style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {(['spill', 'drift', 'vessels'] as const).map((layer) => (
                <label
                  key={layer}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.65rem',
                    fontSize: '0.82rem',
                    color: '#fff',
                    cursor: 'pointer',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={defaultLayers[layer]}
                    onChange={(e) => setDefaultLayers((prev) => ({ ...prev, [layer]: e.target.checked }))}
                    style={{ accentColor: 'var(--color-accent)' }}
                  />
                  <span>
                    {layer === 'spill' ? 'Calibrated Spill Geometry Layer (B3)' : layer === 'drift' ? 'Backward Lagrangian Drift Cone (D3)' : 'AIS Vessel Trajectories & CPA Lines (E2)'}
                  </span>
                </label>
              ))}
            </div>
          )}
        </section>

        {/* Section 4: System Diagnostics */}
        <section
          style={{
            borderRadius: 'var(--radius-md)',
            background: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              padding: '1.25rem',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              cursor: 'pointer',
              background: 'rgba(8, 23, 27, 0.4)',
            }}
            onClick={() => toggleSection('diagnostics')}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
              <Server size={16} color="var(--color-accent)" />
              <h3 style={{ fontSize: '1.05rem', fontWeight: 700, margin: 0, color: '#fff' }}>System Diagnostics</h3>
            </div>
            {sections.diagnostics ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </div>

          {sections.diagnostics && (
            <div style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div className="metric-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
                <div className="metric">
                  <span>Backend Gateway</span>
                  <strong className={isBackendUnavailable ? '' : 'metric--accent'}>
                    {isBackendUnavailable ? 'Offline' : 'Connected (200 OK)'}
                  </strong>
                </div>
                <div className="metric">
                  <span>Gateway Host</span>
                  <strong className="mono-num">127.0.0.1:8000</strong>
                </div>
                <div className="metric">
                  <span>API Protocol</span>
                  <strong className="mono-num">REST v1 (FastAPI)</strong>
                </div>
              </div>
            </div>
          )}
        </section>

        {/* Save Button */}
        <div>
          <button
            className="primary-button"
            type="button"
            onClick={handleSave}
            style={{ padding: '0.6rem 1.5rem' }}
          >
            {saveSuccess ? <Check size={14} /> : null}
            <span>{saveSuccess ? 'Preferences Saved' : 'Save Preferences'}</span>
          </button>
        </div>
      </div>
    </div>
  )
}
