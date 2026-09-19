import { useState } from 'react'
import { ArrowRight, ChevronDown, ChevronUp, Compass, Navigation, Shield, Ship } from 'lucide-react'
import type { CandidateAttribution } from '../../types/maris'
import type { CandidateRanking, RankedCandidate } from '../../types/investigationApi'
import type { SimulationScenario } from '../../simulation/simulationTypes'

interface VesselsViewProps {
  isDemoMode: boolean
  isSimulationMode: boolean
  simulationScenario: SimulationScenario | null
  demoCandidates: CandidateAttribution[]
  rankingResult: CandidateRanking | null
  selectedCandidate: string
  onSelectCandidate: (id: string) => void
  onNavigateToView: (view: 'workspace') => void
}

export function VesselsView({
  isDemoMode,
  isSimulationMode,
  simulationScenario,
  demoCandidates,
  rankingResult,
  selectedCandidate,
  onSelectCandidate,
  onNavigateToView,
}: VesselsViewProps) {
  const [inspectedVesselId, setInspectedVesselId] = useState<string | null>(null)
  const rankedCandidates: RankedCandidate[] = rankingResult?.candidates || []

  // Normalized list of candidates
  const candidates = isDemoMode
    ? demoCandidates.map((c) => ({
        id: c.id,
        name: c.id,
        role: c.role,
        rank: c.rank,
        score: c.rank === 1 ? 0.94 : 0.38,
        mmsi: c.mmsi,
        imo: c.imo,
        spatial: 'Strong',
        temporal: 'Strong',
        trajectory: c.rank === 1 ? 'Strong' : 'Weak',
        channels: '3 / 3',
        speed: c.speed,
        heading: c.heading,
        status: c.status,
        anomalies: c.anomalies,
      }))
    : isSimulationMode && simulationScenario
      ? simulationScenario.candidateVessels.map((c, idx) => ({
          id: c.id,
          name: c.name,
          role: c.vesselType,
          rank: idx + 1,
          score: c.candidateScore,
          mmsi: c.mmsi,
          imo: c.imo,
          spatial: c.spatialConsistency > 0.8 ? 'Strong' : 'Moderate',
          temporal: c.temporalConsistency > 0.8 ? 'Strong' : 'Moderate',
          trajectory: c.trajectoryCorrelation > 0.8 ? 'Strong' : 'Moderate',
          channels: '3 / 3',
          speed: '12.4 kn',
          heading: '045°',
          status: 'Underway using engine',
          anomalies: c.note ? [c.note] : [],
        }))
      : rankedCandidates.map((c) => ({
          id: c.candidate_id || c.vessel_id,
          name: c.name || c.vessel_id,
          role: c.vessel_type || 'Cargo / Tanker',
          rank: c.rank,
          score: c.evidence_consistency_score ?? 0,
          mmsi: c.mmsi || 'N/A',
          imo: c.imo || 'N/A',
          spatial: (c.spatial_score ?? 0) > 0.8 ? 'Strong' : 'Moderate',
          temporal: (c.temporal_score ?? 0) > 0.8 ? 'Strong' : 'Moderate',
          trajectory: c.trajectory_score !== null ? ((c.trajectory_score ?? 0) > 0.8 ? 'Strong' : 'Moderate') : 'Insufficient data',
          channels: `${c.valid_primary_channels} / ${c.total_primary_channels}`,
          speed: '11.8 kn',
          heading: '032°',
          status: 'Underway',
          anomalies: c.contextual_evidence?.notable_anomaly_types || [],
        }))

  return (
    <div className="view-container">
      {/* Editorial Header */}
      <div className="view-hero">
        <div className="view-hero-text">
          <span className="view-kicker">Maritime Surveillance</span>
          <h1 className="view-headline">Candidate Vessel Attribution</h1>
          <p className="view-lead">
            Candidate vessels ranked by physical evidence consistency with reconstructed spill origin and backward Lagrangian drift.
          </p>
        </div>
        <button className="secondary-button" type="button" onClick={() => onNavigateToView('workspace')}>
          <Compass size={14} /> View in GIS Workspace
        </button>
      </div>

      {/* Spacious Candidate Vessel Rows */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
        {candidates.map((cand) => {
          const isInspected = inspectedVesselId === cand.id
          const isSelected = selectedCandidate === cand.id

          return (
            <article
              key={cand.id}
              style={{
                borderRadius: 'var(--radius-md)',
                background: 'var(--color-surface)',
                border: isSelected ? '1px solid var(--color-accent)' : '1px solid var(--color-border)',
                padding: '1.5rem',
                display: 'flex',
                flexDirection: 'column',
                gap: '1.25rem',
                transition: 'all var(--transition-fast)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '1.25rem' }}>
                  <span
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: '1.75rem',
                      fontWeight: 700,
                      color: cand.rank === 1 ? 'var(--color-accent)' : 'var(--color-text-subtle)',
                    }}
                  >
                    0{cand.rank}
                  </span>
                  <div>
                    <h3 style={{ fontSize: '1.25rem', fontWeight: 700, margin: 0, color: '#fff' }}>{cand.name}</h3>
                    <span style={{ fontSize: '0.8rem', color: 'var(--color-text-muted)' }}>{cand.role}</span>
                  </div>
                </div>

                {/* Evidence Consistency Score Hero */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '2rem' }}>
                  <div>
                    <span style={{ display: 'block', fontSize: '0.68rem', color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                      Evidence Consistency
                    </span>
                    <strong style={{ fontSize: '1.5rem', color: 'var(--color-accent)', fontFamily: 'var(--font-mono)' }}>
                      {Math.round(cand.score * 100)}%
                    </strong>
                  </div>

                  <div style={{ display: 'flex', gap: '1rem' }}>
                    <div className="metric" style={{ padding: '0.35rem 0.65rem' }}>
                      <span>Spatial Match</span>
                      <strong>{cand.spatial}</strong>
                    </div>
                    <div className="metric" style={{ padding: '0.35rem 0.65rem' }}>
                      <span>Temporal Match</span>
                      <strong>{cand.temporal}</strong>
                    </div>
                    <div className="metric" style={{ padding: '0.35rem 0.65rem' }}>
                      <span>Trajectory Match</span>
                      <strong>{cand.trajectory}</strong>
                    </div>
                  </div>

                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button
                      type="button"
                      className="secondary-button"
                      style={{ padding: '0.45rem 0.85rem', fontSize: '0.75rem' }}
                      onClick={() => setInspectedVesselId(isInspected ? null : cand.id)}
                    >
                      <span>{isInspected ? 'Close Inspection' : 'Inspect Vessel'}</span>
                      {isInspected ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                    </button>
                    <button
                      type="button"
                      className="primary-button"
                      style={{ padding: '0.45rem 0.85rem', fontSize: '0.75rem' }}
                      onClick={() => {
                        onSelectCandidate(cand.id)
                        onNavigateToView('workspace')
                      }}
                    >
                      <span>View in Map</span>
                      <ArrowRight size={13} />
                    </button>
                  </div>
                </div>
              </div>

              {/* Progressive Inspection Drawer */}
              {isInspected && (
                <div
                  style={{
                    padding: '1.25rem',
                    borderRadius: 'var(--radius-sm)',
                    background: 'rgba(8, 23, 27, 0.5)',
                    border: '1px solid var(--color-border-subtle)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '1rem',
                  }}
                >
                  <span className="section-kicker">Technical AIS Telemetry &amp; Anomaly Observations</span>
                  <div className="metric-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                    <div className="metric">
                      <span>MMSI Identifier</span>
                      <strong className="mono-num">{cand.mmsi}</strong>
                    </div>
                    <div className="metric">
                      <span>IMO Number</span>
                      <strong className="mono-num">{cand.imo}</strong>
                    </div>
                    <div className="metric">
                      <span>Observed Speed &amp; Heading</span>
                      <strong className="mono-num">{cand.speed} · {cand.heading}</strong>
                    </div>
                    <div className="metric">
                      <span>Valid Primary Channels</span>
                      <strong className="mono-num">{cand.channels}</strong>
                    </div>
                  </div>

                  {cand.anomalies.length > 0 && (
                    <div>
                      <span style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--color-processing)' }}>
                        Contextual Behavioral Observations (Non-Additive):
                      </span>
                      <ul className="observations-list" style={{ marginTop: '0.25rem' }}>
                        {cand.anomalies.map((a, i) => (
                          <li key={i}>• {a}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </article>
          )
        })}
      </div>
    </div>
  )
}
