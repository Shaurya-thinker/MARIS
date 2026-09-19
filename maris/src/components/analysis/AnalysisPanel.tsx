import { useState } from 'react'
import {
  Activity,
  Anchor,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Circle,
  CircleAlert,
  Compass,
  Crosshair,
  HelpCircle,
  Info,
  Loader2,
  Minus,
  Shield,
  Ship,
  Wind,
} from 'lucide-react'
import { driftSummary } from '../../data/driftData'
import type { CandidateAttribution, IncidentData } from '../../types/maris'
import type {
  CandidateExplanation,
  CandidateRanking,
  ExplainabilityReport,
  RankedCandidate,
} from '../../types/investigationApi'
import { PrototypeAiAnalysis } from '../../prototype/PrototypeAiAnalysis'
import type { SimulationScenario } from '../../simulation/simulationTypes'

interface AnalysisPanelProps {
  isDemoMode: boolean
  isSimulationMode?: boolean
  simulationScenario?: SimulationScenario | null
  simulationStageIndex?: number
  simulationStages?: Array<{ id: string; label: string; shortLabel: string; description: string }>
  demoIncident: IncidentData
  demoCandidates: CandidateAttribution[]
  selectedCandidate: string
  onSelectCandidate: (id: string) => void
  prototypeActive: boolean
  rankingResult: CandidateRanking | null
  explainabilityReport: ExplainabilityReport | null
}

function Metric({ label, value, tone = '' }: { label: string; value: string; tone?: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
    </div>
  )
}

function DriftAnalysis() {
  const [showDriftDetails, setShowDriftDetails] = useState(false)

  return (
    <section className="panel-section compact-section drift-analysis">
      <div className="section-title-line">
        <div>
          <h3>Environmental Drift Model</h3>
          <p className="section-caption">Lagrangian Metocean Reconstruction</p>
        </div>
        <Wind size={15} aria-hidden="true" />
      </div>
      <div className="metric-grid">
        <Metric label="Forcing Wind" value="ERA5 10 m Wind" />
        <Metric label="Current" value="CMEMS Surface Currents" />
        <Metric label="Windage" value={`${Math.round(driftSummary.forcing.windageFraction * 100)}% empirical`} />
        <Metric label="Displacement" value={`${driftSummary.result.displacementKm.toFixed(2)} km`} />
      </div>

      <div className="disclosure-block" style={{ marginTop: '0.65rem' }}>
        <button
          type="button"
          className="disclosure-trigger"
          onClick={() => setShowDriftDetails((prev) => !prev)}
        >
          <span>{showDriftDetails ? 'Hide drift parameters' : 'View drift parameters'}</span>
          {showDriftDetails ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        </button>

        {showDriftDetails && (
          <div className="disclosure-content">
            <dl className="info-list">
              <div className="info-row">
                <dt>Integration Step</dt>
                <dd className="mono-num">{driftSummary.forcing.integrationStepMinutes}m RK4 Steps</dd>
              </div>
              <div className="info-row">
                <dt>First SAR Fix</dt>
                <dd className="mono-num">{driftSummary.result.estimatedPositionAtFirstSAR.lat.toFixed(3)}°N, {driftSummary.result.estimatedPositionAtFirstSAR.lon.toFixed(3)}°E</dd>
              </div>
              <div className="info-row">
                <dt>Bearing from Origin</dt>
                <dd className="mono-num">{driftSummary.result.bearingFromOrigin_deg.toFixed(1)}°</dd>
              </div>
              <div className="info-row">
                <dt>Advection Scheme</dt>
                <dd>Lagrangian Backward-in-time</dd>
              </div>
            </dl>
          </div>
        )}
      </div>

      <p className="limitation-note" style={{ margin: '0.5rem 0 0', fontSize: '0.6rem', color: 'var(--color-text-subtle)' }}>
        Uses nearest-grid environmental forcing and prototype windage assumptions; not an operational forecast.
      </p>
    </section>
  )
}

function DemoAttributionFactors({ candidate }: { candidate: CandidateAttribution }) {
  return (
    <div className="candidate-card__factors-section">
      <div className="factors-section-header">
        <span className="factors-section-title">Attribution Factors</span>
        <span className="factors-weight-header">Weight</span>
      </div>
      <div className="factors-table">
        {candidate.factors.map((factor) => (
          <div className="factor-row" key={factor.label}>
            <div className="factor-name-cell">
              {factor.status === 'calculated' || factor.status === 'documented' ? (
                <Check size={11} color="var(--color-accent)" className="factor-icon" />
              ) : (
                <Minus size={11} color="var(--color-text-subtle)" className="factor-icon" />
              )}
              <span className="factor-label">{factor.label}</span>
            </div>
            <span className="factor-weight mono-num">{factor.weight}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function AnalysisPanel({
  isDemoMode,
  isSimulationMode = false,
  simulationScenario,
  simulationStageIndex = 0,
  simulationStages = [],
  demoIncident,
  demoCandidates,
  selectedCandidate,
  onSelectCandidate,
  prototypeActive,
  rankingResult,
  explainabilityReport,
}: AnalysisPanelProps) {
  // 1. Demo Mode
  if (isDemoMode) {
    return (
      <aside className="panel analysis-panel" aria-label="Investigation analysis">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">Stage F2 &amp; F3 (Demo)</span>
            <h2>Attribution Analysis</h2>
          </div>
          <Compass size={17} className="heading-icon" aria-hidden="true" />
        </div>

        <section className="panel-section compact-section">
          <div className="section-title-line">
            <h3>Spill Geometry Analysis</h3>
            <CircleAlert size={14} aria-hidden="true" />
          </div>
          <div className="metric-grid">
            <Metric label="Detection" value="Observed Slick" tone="metric--accent" />
            <Metric label="Est. Area" value={demoIncident.estimatedArea} />
            <Metric label="Coordinates" value={demoIncident.coordinates} />
            <Metric label="Confidence" value={demoIncident.confidence} tone="metric--accent" />
          </div>
        </section>

        <PrototypeAiAnalysis active={prototypeActive} />
        <DriftAnalysis />

        <section className="panel-section compact-section">
          <div className="section-title-line">
            <h3>Origin Zone (D3)</h3>
            <Crosshair size={14} aria-hidden="true" />
          </div>
          <div className="metric-grid">
            <Metric label="Status" value="Reconstructed Envelope" tone="metric--pending" />
            <Metric label="Centroid" value={demoIncident.origin} />
            <Metric label="Time Window" value={demoIncident.originWindow} />
            <Metric label="Drift Confidence" value="High (ECMWF)" />
          </div>
        </section>

        <section className="panel-section candidates-section">
          <div className="section-title-line">
            <div>
              <h3>Candidate Vessels (F2)</h3>
              <p className="section-caption">Historical Benchmark Ranking</p>
            </div>
            <Anchor size={14} aria-hidden="true" />
          </div>
          <p className="ranking-notice">
            Highest-ranked candidate indicates investigation priority based on physical evidence correlation.
          </p>
          <div className="candidate-list">
            {demoCandidates.map((candidate) => {
              const isHighestRank = candidate.rank === 1
              const isSelected = selectedCandidate === candidate.id

              return (
                <button
                  key={candidate.id}
                  className={`candidate-card ${isHighestRank ? 'candidate-card--highest' : 'candidate-card--supporting'}${isSelected ? ' is-selected' : ''}`}
                  type="button"
                  onClick={() => onSelectCandidate(candidate.id)}
                >
                  {/* Top: Rank & Status & Arrow */}
                  <div className="candidate-card__header">
                    <div className="candidate-card__rank-group">
                      <span className={`candidate-rank ${isHighestRank ? 'candidate-rank--accent' : 'candidate-rank--slate'}`}>
                        0{candidate.rank}
                      </span>
                      <span className="candidate-card__status-text">
                        {candidate.overallLabel}
                      </span>
                    </div>
                    <span className="candidate-arrow">
                      <ChevronRight size={14} />
                    </span>
                  </div>

                  {/* Candidate Identity */}
                  <div className="candidate-card__identity">
                    <strong className="candidate-card__name">{candidate.id}</strong>
                    {candidate.role && (
                      <span className="candidate-card__role">{candidate.role}</span>
                    )}
                  </div>

                  {/* Identifiers Grid */}
                  <div className="candidate-card__identifiers">
                    <div className="identifier-item">
                      <span className="identifier-label">MMSI</span>
                      <span className="identifier-val mono-num">{candidate.mmsi}</span>
                    </div>
                    <div className="identifier-item">
                      <span className="identifier-label">IMO</span>
                      <span className="identifier-val mono-num">{candidate.imo}</span>
                    </div>
                  </div>

                  {/* Attribution Factors */}
                  <DemoAttributionFactors candidate={candidate} />
                </button>
              )
            })}
          </div>
        </section>
      </aside>
    )
  }

  // 2. Simulation Mode
  if (isSimulationMode && simulationScenario) {
    const selectedSimulationCandidate =
      simulationScenario.candidateVessels.find((candidate) => candidate.id === selectedCandidate) ??
      simulationScenario.candidateVessels[0]

    const analysisSteps = (simulationStages.length > 0 ? simulationStages.slice(1) : []).map((stage) => ({
      id: stage.id,
      label: stage.label,
      shortLabel: stage.shortLabel,
      description: stage.description,
    }))

    const isFinalSimulationState = simulationStageIndex >= Math.max(simulationStages.length - 1, 0)

    if (!isFinalSimulationState) {
      const activeIndex = Math.min(Math.max(simulationStageIndex, 0), Math.max(analysisSteps.length - 1, 0))

      return (
        <aside className="panel analysis-panel" aria-label="Investigation analysis">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">Simulation Analysis</span>
              <h2>{simulationScenario.name}</h2>
            </div>
            <Compass size={17} className="heading-icon" aria-hidden="true" />
          </div>

          <section className="panel-section compact-section">
            <div className="section-title-line">
              <div>
                <h3>Analysis in progress</h3>
                <p className="section-caption">Deterministic simulation workflow</p>
              </div>
              <Loader2 size={14} aria-hidden="true" className="spinner" />
            </div>
            <div className="metric-grid">
              <Metric label="Current step" value={analysisSteps[activeIndex]?.shortLabel || 'Preparing analysis'} tone="metric--accent" />
              <Metric label="Progress" value={`${Math.round(((activeIndex + 1) / Math.max(analysisSteps.length, 1)) * 100)}%`} />
            </div>
          </section>

          <section className="panel-section">
            <div className="section-title-line">
              <div>
                <h3>Processing chain</h3>
                <p className="section-caption">Pending → processing → completed</p>
              </div>
              <Activity size={14} aria-hidden="true" />
            </div>
            <div style={{ display: 'grid', gap: '8px' }}>
              {analysisSteps.map((step, index) => {
                const isCompleted = index < activeIndex
                const isActive = index === activeIndex
                const status = isCompleted ? 'completed' : isActive ? 'processing' : 'pending'

                return (
                  <div
                    key={step.id}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: '10px',
                      padding: '8px 10px',
                      borderRadius: 'var(--radius-xs)',
                      border: `1px solid ${status === 'completed' ? 'rgba(69, 194, 177, 0.35)' : status === 'processing' ? 'rgba(98, 174, 232, 0.5)' : 'rgba(255, 255, 255, 0.08)'}`,
                      background: status === 'completed' ? 'rgba(69, 194, 177, 0.08)' : status === 'processing' ? 'rgba(98, 174, 232, 0.08)' : 'rgba(13, 34, 39, 0.4)',
                      opacity: status === 'pending' ? 0.6 : 1,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 20, minWidth: 20, height: 20, marginTop: 1 }}>
                      {status === 'completed' ? <Check size={13} color="var(--color-accent)" /> : status === 'processing' ? <Loader2 size={13} className="spinner" color="var(--color-info)" /> : <Circle size={8} color="var(--color-text-subtle)" />}
                    </div>
                    <div style={{ flex: 1 }}>
                      <strong style={{ display: 'block', fontSize: '0.78rem', marginBottom: 2 }}>{step.shortLabel}</strong>
                      <small style={{ display: 'block', color: 'var(--color-text-muted)', fontSize: '0.68rem' }}>{step.description}</small>
                    </div>
                  </div>
                )
              })}
            </div>
          </section>

          <section className="panel-section compact-section">
            <div className="section-title-line">
              <div>
                <h3>Investigation report</h3>
                <p className="section-caption">Simulation case summary</p>
              </div>
              <Info size={14} aria-hidden="true" />
            </div>
            <div className="metric-grid">
              <Metric label="Records found" value={String(simulationScenario.databaseSummary.simulatedRecordsFound)} />
              <Metric label="Relevant matches" value={String(simulationScenario.databaseSummary.spatiallyRelevant)} />
              <Metric label="Mode" value="Simulation Mode" tone="metric--accent" />
            </div>
          </section>

          <div className="scientific-disclaimer">
            <Shield size={14} className="disclaimer-icon" />
            <p>Simulation Mode · Synthetic data only · Analysis is running in the frontend demonstration layer.</p>
          </div>
        </aside>
      )
    }

    return (
      <aside className="panel analysis-panel" aria-label="Investigation analysis">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">Simulation Analysis</span>
            <h2>{simulationScenario.name}</h2>
          </div>
          <Compass size={17} className="heading-icon" aria-hidden="true" />
        </div>

        <section className="panel-section compact-section">
          <div className="section-title-line">
            <h3>Spill Dimensions</h3>
            <CircleAlert size={14} aria-hidden="true" />
          </div>
          <div className="metric-grid">
            <Metric label="Detection" value="Synthetic Anomaly" tone="metric--accent" />
            <Metric label="Area" value={`${simulationScenario.spillAreaKm2.toFixed(1)} km²`} />
            <Metric label="Displacement" value={`${simulationScenario.environmentalDrift.displacementKm.toFixed(1)} km`} />
            <Metric label="Confidence" value={`${Math.round(simulationScenario.evidenceSummary.spatial * 100)}%`} tone="metric--accent" />
          </div>
        </section>

        <section className="panel-section compact-section">
          <div className="section-title-line">
            <h3>Source &amp; Drift Dynamics</h3>
            <Crosshair size={14} aria-hidden="true" />
          </div>
          <div className="metric-grid">
            <Metric label="Wind" value={simulationScenario.environmentalDrift.wind} />
            <Metric label="Current" value={simulationScenario.environmentalDrift.current} />
            <Metric label="Source Envelope" value={simulationScenario.environmentalDrift.sourceZone} />
            <Metric label="Evidence" value={simulationScenario.evidenceSummary.availability} />
          </div>
        </section>

        <section className="panel-section candidates-section">
          <div className="section-title-line">
            <div>
              <h3>Candidate Vessels (F2)</h3>
              <p className="section-caption">Synthetic Consistency Ranking</p>
            </div>
            <Anchor size={14} aria-hidden="true" />
          </div>
          <p className="ranking-notice">
            Synthetic candidate ranking for frontend showcase only.
          </p>
          <div className="candidate-list">
            {simulationScenario.candidateVessels.map((candidate) => {
              const isSelected = selectedCandidate === candidate.id

              return (
                <button
                  key={candidate.id}
                  className={`candidate-card ${candidate.isCandidate ? 'candidate-card--highest' : 'candidate-card--supporting'}${isSelected ? ' is-selected' : ''}`}
                  type="button"
                  onClick={() => onSelectCandidate(candidate.id)}
                >
                  <div className="candidate-card__header">
                    <div className="candidate-card__rank-group">
                      <span className={`candidate-rank ${candidate.isCandidate ? 'candidate-rank--accent' : 'candidate-rank--slate'}`}>
                        {candidate.isCandidate ? '01' : '99'}
                      </span>
                      <span className="candidate-card__status-text">
                        {candidate.isCandidate ? 'Primary Candidate' : 'Secondary Vessel'}
                      </span>
                    </div>
                    <span className="candidate-arrow">
                      <ChevronRight size={14} />
                    </span>
                  </div>

                  <div className="candidate-card__identity">
                    <strong className="candidate-card__name">{candidate.name}</strong>
                    <span className="candidate-card__role">{candidate.vesselType}</span>
                  </div>

                  {candidate.note && (
                    <div className="candidate-card__identifiers">
                      <div className="identifier-item" style={{ gridColumn: 'span 2' }}>
                        <span className="identifier-label">Status</span>
                        <span className="identifier-val">{candidate.note}</span>
                      </div>
                    </div>
                  )}
                </button>
              )
            })}
          </div>
        </section>

        <section className="panel-section compact-section">
          <div className="section-title-line">
            <div>
              <h3>Investigation report</h3>
              <p className="section-caption">Simulation case summary</p>
            </div>
            <Info size={14} aria-hidden="true" />
          </div>
          <div className="metric-grid">
            <Metric label="Records found" value={String(simulationScenario.databaseSummary.simulatedRecordsFound)} />
            <Metric label="Relevant matches" value={String(simulationScenario.databaseSummary.spatiallyRelevant)} />
            <Metric label="Search radius" value={`${simulationScenario.databaseSummary.searchRadiusKm} km`} />
            <Metric label="Mode" value="Simulation Mode" tone="metric--accent" />
          </div>
        </section>

        <section className="panel-section compact-section">
          <div className="section-title-line">
            <div>
              <h3>Evaluation Narrative (F3)</h3>
              <p className="section-caption">Explainability Report</p>
            </div>
            <Shield size={14} aria-hidden="true" />
          </div>
          <div className="explanation-block">
            <h4>Attribution Summary</h4>
            <p className="channel-desc">{simulationScenario.finalExplanation}</p>
          </div>
        </section>
      </aside>
    )
  }

  // 3. Live G1 Investigation Mode
  const rankedCandidates: RankedCandidate[] = rankingResult?.candidates || []
  const selectedRanked = rankedCandidates.find(
    (c) => c.candidate_id === selectedCandidate || c.vessel_id === selectedCandidate
  ) || rankedCandidates[0]

  const selectedExplanation: CandidateExplanation | undefined = explainabilityReport?.candidates.find(
    (e) => e.candidate_id === selectedCandidate || e.vessel_id === selectedCandidate
  ) || explainabilityReport?.candidates[0]

  return (
    <aside className="panel analysis-panel" aria-label="Investigation analysis">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Stage F2 &amp; F3</span>
          <h2>Candidate Ranking &amp; Attribution</h2>
        </div>
        <Compass size={17} className="heading-icon" aria-hidden="true" />
      </div>

      {/* Candidate Ranking List */}
      <section className="panel-section candidates-section">
        <div className="section-title-line">
          <div>
            <h3>Candidate Vessels</h3>
            <p className="section-caption">
              {rankedCandidates.length > 0
                ? `${rankedCandidates.length} candidate(s) ranked by physical evidence consistency`
                : 'No candidates ranked'}
            </p>
          </div>
          <Anchor size={14} aria-hidden="true" />
        </div>

        <p className="ranking-notice">
          Ranked by comparative consistency with physical evidence (Spatial 0.40, Temporal 0.35, Trajectory 0.25). Does NOT establish legal culpability.
        </p>

        {rankedCandidates.length === 0 ? (
          <div className="empty-results-box" style={{ padding: '1.5rem', textAlign: 'center' }}>
            <HelpCircle size={22} color="var(--color-text-subtle)" />
            <p style={{ margin: '0.4rem 0 0.1rem', fontSize: '0.75rem', color: 'var(--color-text-muted)' }}>
              Candidate ranking not yet available.
            </p>
            <small style={{ color: 'var(--color-text-subtle)', fontSize: '0.65rem' }}>
              Run the investigation workflow to execute stages B1 through F3.
            </small>
          </div>
        ) : (
          <div className="candidate-list">
            {rankedCandidates.map((cand) => {
              const isSelected =
                (selectedRanked && (selectedRanked.candidate_id === cand.candidate_id || selectedRanked.vessel_id === cand.vessel_id)) ||
                selectedCandidate === cand.candidate_id ||
                selectedCandidate === cand.vessel_id

              const scoreDisplay =
                cand.evidence_consistency_score !== null
                  ? cand.evidence_consistency_score.toFixed(2)
                  : 'N/A'

              return (
                <button
                  key={cand.candidate_id || cand.vessel_id}
                  className={`candidate-card${isSelected ? ' is-selected' : ''}`}
                  type="button"
                  onClick={() => onSelectCandidate(cand.candidate_id || cand.vessel_id)}
                >
                  <span className="candidate-rank">0{cand.rank}</span>
                  <div className="candidate-details">
                    <div className="candidate-name-row">
                      <strong>{cand.name || cand.vessel_id}</strong>
                      {cand.vessel_type && <span className="vessel-tag">{cand.vessel_type}</span>}
                    </div>
                    <div className="evidence-score-row">
                      <span className="score-label">Evidence Consistency Score:</span>
                      <span className="score-value">{scoreDisplay}</span>
                    </div>
                    <div className="evidence-availability-row" style={{ fontSize: '0.68rem', color: 'var(--color-text-muted)', margin: '0.15rem 0' }}>
                      <span>Evidence Availability: </span>
                      <strong style={{ color: 'var(--color-text-primary)' }}>
                        {cand.valid_primary_channels} / {cand.total_primary_channels}
                      </strong>
                    </div>
                    {(cand.mmsi || cand.imo) && (
                      <span className="evidence-placeholder">
                        {cand.mmsi ? `MMSI: ${cand.mmsi}` : ''} {cand.imo ? `· IMO: ${cand.imo}` : ''}
                      </span>
                    )}
                  </div>
                  <span className="candidate-arrow">
                    <ChevronRight size={14} />
                  </span>
                </button>
              )
            })}
          </div>
        )}
      </section>

      {/* Selected Candidate Detailed Explainability */}
      {selectedExplanation && (
        <section className="panel-section candidate-explainability">
          <div className="section-title-line">
            <div>
              <h3>Candidate Evaluation (F3)</h3>
              <p className="section-caption">
                Rank #{selectedExplanation.rank} — {selectedExplanation.name || selectedExplanation.vessel_id}
              </p>
            </div>
            <Shield size={15} aria-hidden="true" />
          </div>

          <div className="metric-grid">
            <Metric
              label="Consistency Score"
              value={
                selectedExplanation.evidence_consistency_score !== null
                  ? selectedExplanation.evidence_consistency_score.toFixed(2)
                  : 'N/A'
              }
              tone="metric--accent"
            />
            <Metric
              label="Consistency Band"
              value={selectedExplanation.evidence_consistency_level}
              tone={
                selectedExplanation.evidence_consistency_level === 'HIGH'
                  ? 'metric--accent'
                  : selectedExplanation.evidence_consistency_level === 'MODERATE'
                    ? 'metric--pending'
                    : 'metric--muted'
              }
            />
            <Metric
              label="Evidence Availability"
              value={`${selectedExplanation.valid_primary_channels} / ${selectedExplanation.total_primary_channels} (${selectedExplanation.evidence_availability_band})`}
            />
          </div>

          {/* Primary Evidence Channels */}
          <div className="explanation-block">
            <h4>Primary Evidence Channels</h4>
            <div className="channel-row">
              <div className="channel-heading">
                <span>Spatial Proximity:</span>
                <strong>
                  {selectedExplanation.spatial_score !== null
                    ? selectedExplanation.spatial_score.toFixed(2)
                    : 'Insufficient data'}
                </strong>
              </div>
              <p className="channel-desc">{selectedExplanation.spatial_explanation}</p>
            </div>

            <div className="channel-row">
              <div className="channel-heading">
                <span>Temporal Synchronization:</span>
                <strong>
                  {selectedExplanation.temporal_score !== null
                    ? selectedExplanation.temporal_score.toFixed(2)
                    : 'Insufficient data'}
                </strong>
              </div>
              <p className="channel-desc">{selectedExplanation.temporal_explanation}</p>
            </div>

            <div className="channel-row">
              <div className="channel-heading">
                <span>Trajectory Alignment:</span>
                <strong>
                  {selectedExplanation.trajectory_score !== null
                    ? selectedExplanation.trajectory_score.toFixed(2)
                    : 'Insufficient data'}
                </strong>
              </div>
              <p className="channel-desc">{selectedExplanation.trajectory_explanation}</p>
            </div>
          </div>

          {/* Contextual Behavioral Intelligence */}
          <div className="explanation-block contextual-block">
            <h4>{selectedExplanation.behavioral_context.label || 'Contextual behavioral observations'}</h4>
            <p className="non-contribution-note">
              {selectedExplanation.behavioral_context.numerical_contribution_statement}
            </p>
            {selectedExplanation.behavioral_context.observations.length > 0 ? (
              <ul className="observations-list">
                {selectedExplanation.behavioral_context.observations.map((obs, idx) => (
                  <li key={idx}>• {obs}</li>
                ))}
              </ul>
            ) : (
              <p className="no-anomalies-note" style={{ fontSize: '0.62rem', color: 'var(--color-text-subtle)', margin: 0 }}>
                No anomalous behavioral patterns detected.
              </p>
            )}
          </div>

          {/* Forward Drift Cross-Check */}
          <div className="explanation-block crosscheck-block">
            <div className="crosscheck-header">
              <h4>{selectedExplanation.drift_cross_check.label || 'Forward drift cross-check'}</h4>
              <span className={`crosscheck-badge crosscheck-badge--${selectedExplanation.drift_cross_check.status.toLowerCase()}`}>
                {selectedExplanation.drift_cross_check.status}
              </span>
            </div>
            <p className="channel-desc">{selectedExplanation.drift_cross_check.explanation}</p>
            <small className="non-contribution-note">
              {selectedExplanation.drift_cross_check.non_additive_statement}
            </small>
          </div>

          {/* Scientific Limitations & Legal Disclaimer */}
          <div className="scientific-disclaimer">
            <Shield size={14} className="disclaimer-icon" />
            <p>{selectedExplanation.scientific_disclaimer}</p>
          </div>
        </section>
      )}
    </aside>
  )
}
