import { useState } from 'react'
import { ChevronDown, ChevronUp, Compass, Shield, TrendingUp, Waves, Wind } from 'lucide-react'
import type { CandidateRanking, ExplainabilityReport } from '../../types/investigationApi'
import type { SimulationScenario } from '../../simulation/simulationTypes'

interface AnalyticsViewProps {
  isDemoMode: boolean
  isSimulationMode: boolean
  simulationScenario: SimulationScenario | null
  rankingResult: CandidateRanking | null
  explainabilityReport: ExplainabilityReport | null
  onNavigateToView: (view: 'workspace') => void
}

export function AnalyticsView({
  isDemoMode,
  isSimulationMode,
  simulationScenario,
  rankingResult,
  explainabilityReport,
  onNavigateToView,
}: AnalyticsViewProps) {
  const [showModelDetails, setShowModelDetails] = useState(false)

  return (
    <div className="view-container">
      {/* Editorial Header */}
      <div className="view-hero">
        <div className="view-hero-text">
          <span className="view-kicker">Attribution Analytics</span>
          <h1 className="view-headline">Scientific Attribution &amp; Drift Dynamics</h1>
          <p className="view-lead">
            Lagrangian hydrodynamic advection modeling, backward trajectory reconstruction, and multi-channel evidence fusion.
          </p>
        </div>
        <button className="secondary-button" type="button" onClick={() => onNavigateToView('workspace')}>
          <Compass size={14} /> Back to GIS Workspace
        </button>
      </div>

      {/* Large Primary Visualization: Drift Displacement Hero */}
      <section style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
        <div className="section-title-line">
          <div>
            <span className="section-kicker">Lagrangian Advection Simulation</span>
            <h2 style={{ fontSize: '1.35rem', fontWeight: 700, margin: 0, color: '#fff' }}>Drift Displacement</h2>
          </div>
          <span className="status-badge status-badge--completed">Runge-Kutta 4th Order</span>
        </div>

        {/* Cinematic SVG Drift Visualization Canvas */}
        <div
          style={{
            height: '240px',
            width: '100%',
            borderRadius: 'var(--radius-md)',
            background: 'linear-gradient(180deg, #091a1f 0%, #051013 100%)',
            border: '1px solid var(--color-border)',
            position: 'relative',
            overflow: 'hidden',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <svg width="100%" height="100%" viewBox="0 0 800 240" style={{ position: 'absolute', inset: 0 }}>
            <defs>
              <linearGradient id="driftGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="var(--color-accent)" stopOpacity="0.8" />
                <stop offset="50%" stopColor="var(--color-info)" stopOpacity="0.8" />
                <stop offset="100%" stopColor="var(--color-processing)" stopOpacity="0.9" />
              </linearGradient>
            </defs>
            {/* Grid Lines */}
            <line x1="0" y1="60" x2="800" y2="60" stroke="rgba(255,255,255,0.03)" strokeWidth="1" />
            <line x1="0" y1="120" x2="800" y2="120" stroke="rgba(255,255,255,0.03)" strokeWidth="1" />
            <line x1="0" y1="180" x2="800" y2="180" stroke="rgba(255,255,255,0.03)" strokeWidth="1" />
            <line x1="200" y1="0" x2="200" y2="240" stroke="rgba(255,255,255,0.03)" strokeWidth="1" />
            <line x1="400" y1="0" x2="400" y2="240" stroke="rgba(255,255,255,0.03)" strokeWidth="1" />
            <line x1="600" y1="0" x2="600" y2="240" stroke="rgba(255,255,255,0.03)" strokeWidth="1" />

            {/* Simulated Particle Trajectories */}
            <path
              d="M 120 170 Q 280 140 460 90 T 700 45"
              fill="none"
              stroke="url(#driftGradient)"
              strokeWidth="3"
            />
            <path
              d="M 120 170 Q 260 160 440 105 T 680 55"
              fill="none"
              stroke="rgba(69, 194, 177, 0.2)"
              strokeWidth="1.5"
              strokeDasharray="4 4"
            />
            <path
              d="M 120 170 Q 300 120 480 75 T 720 35"
              fill="none"
              stroke="rgba(98, 174, 232, 0.2)"
              strokeWidth="1.5"
              strokeDasharray="4 4"
            />

            {/* Spill Origin and Detection Points */}
            <circle cx="120" cy="170" r="5" fill="var(--color-processing)" />
            <text x="120" y="195" fill="var(--color-text-muted)" fontSize="11" fontFamily="var(--font-mono)" textAnchor="middle">
              Reconstructed Origin (D3)
            </text>

            <circle cx="700" cy="45" r="5" fill="var(--color-accent)" />
            <text x="700" y="32" fill="var(--color-accent)" fontSize="11" fontFamily="var(--font-mono)" textAnchor="middle">
              Observed Spill (B3)
            </text>
          </svg>
        </div>

        {/* 4 Clean Key Hero Metrics */}
        <div className="telemetry-hero-grid">
          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Estimated Displacement</span>
            <span className="telemetry-hero-value telemetry-hero-value--accent">18.45 km</span>
            <span className="telemetry-hero-caption">Net Euclidean drift distance</span>
          </div>

          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Average Drift Velocity</span>
            <span className="telemetry-hero-value telemetry-hero-value--info">0.78 kn</span>
            <span className="telemetry-hero-caption">Integrated surface transport</span>
          </div>

          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Mean Drift Direction</span>
            <span className="telemetry-hero-value">028° NNE</span>
            <span className="telemetry-hero-caption">True compass heading</span>
          </div>

          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Current / Wind Ratio</span>
            <span className="telemetry-hero-value">72 / 28</span>
            <span className="telemetry-hero-caption">CMEMS vs ERA5 influence</span>
          </div>
        </div>

        {/* Progressive Disclosure: View Attribution Model */}
        <div className="disclosure-block" style={{ marginTop: '0.5rem' }}>
          <button
            type="button"
            className="disclosure-trigger"
            onClick={() => setShowModelDetails(!showModelDetails)}
          >
            <span>{showModelDetails ? 'Hide attribution model' : 'View attribution model'}</span>
            {showModelDetails ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>

          {showModelDetails && (
            <div className="disclosure-content" style={{ gap: '1.25rem' }}>
              <div className="metric-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
                <div className="metric">
                  <span>Spatial Proximity Weight</span>
                  <strong className="metric--accent">40.0%</strong>
                  <small style={{ color: 'var(--color-text-subtle)', fontSize: '0.62rem' }}>D3 origin zone intersection</small>
                </div>
                <div className="metric">
                  <span>Temporal Match Weight</span>
                  <strong style={{ color: 'var(--color-info)' }}>35.0%</strong>
                  <small style={{ color: 'var(--color-text-subtle)', fontSize: '0.62rem' }}>Release window alignment</small>
                </div>
                <div className="metric">
                  <span>Trajectory Consistency</span>
                  <strong style={{ color: 'var(--color-processing)' }}>25.0%</strong>
                  <small style={{ color: 'var(--color-text-subtle)', fontSize: '0.62rem' }}>Course vs drift major axis</small>
                </div>
              </div>

              <div
                style={{
                  padding: '1rem',
                  borderRadius: 'var(--radius-sm)',
                  background: 'var(--color-surface)',
                  border: '1px solid var(--color-border)',
                  fontSize: '0.78rem',
                  color: 'var(--color-text-muted)',
                  lineHeight: 1.5,
                }}
              >
                <strong style={{ color: '#fff', display: 'block', marginBottom: '0.25rem' }}>
                  F1 Multi-Channel Normalization Formula
                </strong>
                Evidence Consistency Score = (0.40 × Spatial + 0.35 × Temporal + 0.25 × Trajectory) / Available Channels.
                <br />
                <span style={{ color: 'var(--color-processing)' }}>
                  * Contextual behavioral observations (E3) and forward drift cross-checks (D1) are strictly non-additive (0.00 numerical contribution).
                </span>
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
