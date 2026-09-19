import { useState } from 'react'
import {
  Activity,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Eye,
  EyeOff,
  Info,
  Loader2,
  Play,
  PlusCircle,
  Radio,
  RefreshCw,
  Satellite,
  Ship,
  Sliders,
  Waves,
  Zap,
} from 'lucide-react'
import type { IncidentData } from '../../types/maris'
import type { SimulationScenario } from '../../simulation/simulationTypes'
import type {
  ArtifactSummary,
  InvestigationResponse,
  InvestigationRunRequest,
  InvestigationStatusResponse,
} from '../../types/investigationApi'

interface IncidentPanelProps {
  isDemoMode: boolean
  isSimulationMode?: boolean
  simulationScenario?: SimulationScenario | null
  demoIncident: IncidentData
  liveInvestigation: InvestigationResponse | null
  statusResponse: InvestigationStatusResponse | null
  artifacts: ArtifactSummary[]
  layers: { spill: boolean; drift: boolean; vessels: boolean }
  onToggleLayer: (layer: 'spill' | 'drift' | 'vessels') => void
  onRunWorkflow: (payload?: InvestigationRunRequest) => void
  isRunningWorkflow: boolean
  error?: string | null
  onOpenCreateModal?: () => void
  onRefreshStatus?: () => void
  hasDeferredChecked?: boolean
}

function InfoRow({ label, value, isMono = false }: { label: string; value: string | React.ReactNode; isMono?: boolean }) {
  return (
    <div className="info-row">
      <dt>{label}</dt>
      <dd className={isMono ? 'mono-num' : ''}>{value}</dd>
    </div>
  )
}

export function IncidentPanel({
  isDemoMode,
  isSimulationMode = false,
  simulationScenario,
  demoIncident,
  liveInvestigation,
  statusResponse,
  artifacts,
  layers,
  onToggleLayer,
  onRunWorkflow,
  isRunningWorkflow,
  error,
  onOpenCreateModal,
  onRefreshStatus,
  hasDeferredChecked,
}: IncidentPanelProps) {
  const [sarPath, setSarPath] = useState('')
  const [driftHours, setDriftHours] = useState('')
  const [lookbackHours, setLookbackHours] = useState('')
  const [showConfig, setShowConfig] = useState(false)
  const [showTechDetails, setShowTechDetails] = useState(false)

  // Demo mode: Historical Corsica Demo Case
  if (isDemoMode) {
    return (
      <aside className="panel incident-panel" aria-label="Incident and map controls">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">Benchmark Case</span>
            <h2>Incident Overview</h2>
          </div>
          <Activity size={17} className="heading-icon" aria-hidden="true" />
        </div>

        <section className="panel-section">
          <h3>Key Telemetry</h3>
          <dl className="info-list">
            <InfoRow label="Incident ID" value={demoIncident.id} isMono />
            <InfoRow label="Region" value={demoIncident.region} />
            <InfoRow label="Status" value={<span className="status-badge status-badge--completed">{demoIncident.status}</span>} />
            <InfoRow label="Detection Time" value={demoIncident.detectionTime} isMono />
          </dl>
        </section>

        <section className="panel-section">
          <div className="section-title-line">
            <h3>SAR Evidence</h3>
            <Satellite size={14} aria-hidden="true" />
          </div>
          <div className="satellite-frame">
            <img
              className="satellite-evidence"
              src={demoIncident.satelliteImage}
              alt="Sentinel-1A SAR visualization of the reconstructed Corsica oil slick"
            />
            <span className="satellite-overlay-tag">S1A SAR C-BAND</span>
          </div>
          <dl className="info-list" style={{ marginTop: '0.5rem' }}>
            <InfoRow label="Sensor Source" value={demoIncident.satelliteSource} />
            <InfoRow label="Acquired UTC" value={demoIncident.acquisitionTime} isMono />
          </dl>
        </section>

        <section className="panel-section">
          <div className="section-title-line">
            <h3>Spill Geometry</h3>
            <Waves size={14} aria-hidden="true" />
          </div>
          <dl className="info-list">
            <InfoRow label="Est. Area" value={demoIncident.estimatedArea} isMono />
            <InfoRow label="Confidence" value={demoIncident.confidence} isMono />
          </dl>

          {/* Progressive Disclosure: Technical Metadata */}
          <div className="disclosure-block" style={{ marginTop: '0.65rem' }}>
            <button
              type="button"
              className="disclosure-trigger"
              onClick={() => setShowTechDetails((prev) => !prev)}
            >
              <span>{showTechDetails ? 'Hide technical metadata' : 'View technical metadata'}</span>
              {showTechDetails ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            </button>

            {showTechDetails && (
              <div className="disclosure-content">
                <dl className="info-list">
                  <InfoRow label="Scene Status" value={demoIncident.sceneStatus} />
                  <InfoRow label="Geographic Extent" value="9.08°E, 42.90°N to 9.75°E, 43.52°N" isMono />
                  <InfoRow label="Last Reconstructed" value={demoIncident.lastUpdated} isMono />
                  <InfoRow label="Target Slick Kind" value="Calibrated Multi-Polygon" />
                </dl>
              </div>
            )}
          </div>
        </section>

        <section className="panel-section controls-section">
          <h3>GIS Layer Controls</h3>
          <div className="layer-controls">
            <LayerButton label="Calibrated Spill Overlay" icon={Waves} active={layers.spill} onClick={() => onToggleLayer('spill')} />
            <LayerButton label="Drift Trajectories" icon={Activity} active={layers.drift} onClick={() => onToggleLayer('drift')} />
            <LayerButton label="AIS Vessel Tracks" icon={Ship} active={layers.vessels} onClick={() => onToggleLayer('vessels')} />
          </div>
        </section>
      </aside>
    )
  }

  // Simulation mode
  if (isSimulationMode && simulationScenario) {
    return (
      <aside className="panel incident-panel" aria-label="Incident and map controls">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">Simulation Showcase</span>
            <h2>{simulationScenario.name}</h2>
          </div>
          <Activity size={17} className="heading-icon" aria-hidden="true" />
        </div>

        <section className="panel-section">
          <h3>Case Summary</h3>
          <dl className="info-list">
            <InfoRow label="Scenario ID" value={`simulation-${simulationScenario.id}`} />
            <InfoRow label="Region" value={simulationScenario.region} />
            <InfoRow label="Status" value="Synthetic scenario active" />
            <InfoRow label="Observed" value={new Date(simulationScenario.timestamp).toLocaleString()} />
            <InfoRow label="Mode" value="Frontend-only demonstration" />
          </dl>
        </section>

        <section className="panel-section">
          <div className="section-title-line">
            <h3>Reference Scene</h3>
            <Satellite size={14} aria-hidden="true" />
          </div>
          <div className="satellite-frame">
            <img
              className="satellite-evidence"
              src={simulationScenario.satelliteScene}
              alt={simulationScenario.name}
            />
            <span className="satellite-overlay-tag">SYNTHETIC SAR</span>
          </div>
          <dl className="info-list">
            <InfoRow label="Source" value="Reference SAR Imagery" />
            <InfoRow label="Timestamp" value={simulationScenario.timestamp} />
          </dl>
        </section>

        <section className="panel-section">
          <div className="section-title-line">
            <h3>Spill Dimensions</h3>
            <Waves size={14} aria-hidden="true" />
          </div>
          <dl className="info-list">
            <InfoRow label="Status" value="Synthetic Slick Detected" />
            <InfoRow label="Est. Surface" value={`${simulationScenario.spillAreaKm2.toFixed(1)} km²`} />
            <InfoRow label="Evidence Availability" value={`${simulationScenario.evidenceSummary.availability}`} />
          </dl>
        </section>

        <section className="panel-section controls-section">
          <h3>GIS Layer Controls</h3>
          <div className="layer-controls">
            <LayerButton label="Synthetic Spill Overlay" icon={Waves} active={layers.spill} onClick={() => onToggleLayer('spill')} />
            <LayerButton label="Drift Paths" icon={Activity} active={layers.drift} onClick={() => onToggleLayer('drift')} />
            <LayerButton label="Simulated Vessel Tracks" icon={Ship} active={layers.vessels} onClick={() => onToggleLayer('vessels')} />
          </div>
        </section>

        <div className="demo-note" style={{ padding: '0.6rem 1rem', fontSize: '0.6rem', color: 'var(--color-text-subtle)', fontFamily: 'var(--font-mono)' }}>
          SIMULATION SHOWCASE MODE — FRONTEND-ONLY DEMONSTRATION
        </div>
      </aside>
    )
  }

  // Live mode — not yet loaded or error
  if (!liveInvestigation) {
    return (
      <aside className="panel incident-panel" aria-label="Incident and map controls">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">Live Investigation</span>
            <h2>{error ? 'Unavailable' : 'Loading…'}</h2>
          </div>
          <Activity size={17} className="heading-icon" aria-hidden="true" />
        </div>
        {error && (
          <div className="error-callout" role="alert" style={{ margin: '1rem' }}>
            <AlertTriangle size={16} />
            <span>{error}</span>
          </div>
        )}
        {!error && (
          <div className="panel-section" style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
            <Loader2 size={16} className="spinner" />
            <span style={{ color: 'var(--color-text-muted)', fontSize: '0.75rem' }}>Loading investigation details…</span>
          </div>
        )}
      </aside>
    )
  }

  // Live G1 Investigation View
  const aoi = liveInvestigation.area_of_interest
  const aoiDescription =
    aoi?.kind === 'bbox'
      ? `[${aoi.bbox.west.toFixed(2)}°W, ${aoi.bbox.south.toFixed(2)}°S, ${aoi.bbox.east.toFixed(2)}°E, ${aoi.bbox.north.toFixed(2)}°N]`
      : aoi?.kind === 'polygon'
      ? `GeoJSON Polygon (${aoi.polygon.coordinates[0]?.length || 0} pts)`
      : 'Area of Interest: N/A'

  const safeArtifacts = Array.isArray(artifacts) ? artifacts : []
  const spillArtifact = safeArtifacts.find(
    (a) => a.asset_type === 'spill_geometry' || a.source === 'spill_detection'
  )
  const spillMetadata = spillArtifact?.metadata || {}
  const spillCentroid = spillMetadata.centroid as { longitude: number; latitude: number } | undefined
  const spillAreaM2 = (spillMetadata.total_area_m2 || spillMetadata.area) as number | undefined
  const spillConfidence = spillMetadata.confidence as number | undefined

  const activeStatus = statusResponse?.status || liveInvestigation.status || 'UNKNOWN'
  const errors = statusResponse?.errors || []

  const isCompleted = !isRunningWorkflow && activeStatus === 'COMPLETED'
  const isProcessing = isRunningWorkflow || activeStatus === 'PROCESSING'
  const isFailed = activeStatus === 'FAILED'

  const handleRunClick = () => {
    const payload: InvestigationRunRequest = {}
    if (sarPath.trim()) {
      payload.sentinel1_artifact_path = sarPath.trim()
    }
    const dVal = parseFloat(driftHours)
    if (driftHours.trim() && !Number.isNaN(dVal)) {
      payload.drift_hours = dVal
    }
    const lVal = parseFloat(lookbackHours)
    if (lookbackHours.trim() && !Number.isNaN(lVal)) {
      payload.lookback_hours = lVal
    }
    onRunWorkflow(Object.keys(payload).length > 0 ? payload : undefined)
  }

  return (
    <aside className="panel incident-panel" aria-label="Incident and map controls">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Live Investigation</span>
          <h2>{liveInvestigation.name}</h2>
        </div>
        <Activity size={17} className="heading-icon" aria-hidden="true" />
      </div>

      {error && (
        <div className="error-callout" role="alert" style={{ margin: '0.75rem 1rem' }}>
          <AlertTriangle size={16} />
          <span>{error}</span>
        </div>
      )}

      {errors.length > 0 && (
        <div className="stage-error-box" role="alert" style={{ margin: '0.75rem 1rem' }}>
          <div className="stage-error-header">
            <AlertTriangle size={15} />
            <strong>Pipeline Execution Error</strong>
          </div>
          {errors.map((err, idx) => (
            <div key={idx} className="stage-error-item">
              <span className="stage-tag">Stage {err.stage}</span>
              <strong className="error-tag">{err.error}</strong>
              <p className="error-desc">{err.message}</p>
            </div>
          ))}
        </div>
      )}

      <section className="panel-section">
        <h3>Investigation Details</h3>
        <dl className="info-list">
          <InfoRow label="Case ID" value={liveInvestigation.id} />
          <InfoRow label="Status" value={<span className={`status-badge status-badge--${activeStatus.toLowerCase()}`}>{activeStatus}</span>} />
          <InfoRow label="AOI Bounding Box" value={aoiDescription} />
          <InfoRow
            label="Time Window"
            value={
              liveInvestigation.time_window?.start
                ? `${new Date(liveInvestigation.time_window.start).toLocaleDateString()} – ${new Date(liveInvestigation.time_window.end).toLocaleDateString()}`
                : 'N/A'
            }
          />
          <InfoRow
            label="Registered"
            value={liveInvestigation.created_at ? new Date(liveInvestigation.created_at).toLocaleDateString() : 'N/A'}
          />
        </dl>
      </section>

      <section className="panel-section">
        <div className="section-title-line">
          <h3>Spill Detection (B3)</h3>
          <Waves size={14} aria-hidden="true" />
        </div>
        <dl className="info-list">
          <InfoRow
            label="Detection Status"
            value={
              spillArtifact
                ? spillMetadata.detected
                  ? 'Confirmed slick detected'
                  : 'No slick detected'
                : 'Pending B3 Execution'
            }
          />
          {spillAreaM2 !== undefined && (
            <InfoRow
              label="Area Footprint"
              value={`${(spillAreaM2 / 1_000_000).toFixed(3)} km² (${spillAreaM2.toLocaleString()} m²)`}
            />
          )}
          {spillConfidence !== undefined && (
            <InfoRow label="Spatial Confidence" value={`${(spillConfidence * 100).toFixed(0)}%`} />
          )}
          {spillCentroid && (
            <InfoRow
              label="Centroid"
              value={`${spillCentroid.latitude.toFixed(4)}°N, ${spillCentroid.longitude.toFixed(4)}°E`}
            />
          )}
        </dl>
      </section>

      <section className="panel-section controls-section">
        <div className="section-title-line">
          <h3>Pipeline Execution</h3>
          <span className="vessel-tag" style={{ color: 'var(--color-accent)' }}>{artifacts.length} Artifacts</span>
        </div>

        {/* Run Configuration (Server-side SAR path & optional tuning parameters) */}
        <div className="run-config-box">
          <button
            type="button"
            className="run-config-toggle"
            onClick={() => setShowConfig((prev) => !prev)}
            aria-expanded={showConfig}
          >
            <Sliders size={13} />
            <span>Run Configuration (Advanced)</span>
            {showConfig ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>

          {showConfig && (
            <div className="run-config-content">
              <div className="config-form-group">
                <label htmlFor="sar-path-input">Sentinel-1 Artifact Path (Server-side)</label>
                <input
                  id="sar-path-input"
                  type="text"
                  className="config-text-input"
                  placeholder="/server/path/to/S1A_...SAFE.zip"
                  value={sarPath}
                  onChange={(e) => setSarPath(e.target.value)}
                  disabled={isProcessing || isCompleted}
                />
                <p className="config-hint-text">
                  Server-side filesystem path only. Browser upload deferred to G4.
                </p>
              </div>

              <div className="config-form-row">
                <div className="config-form-group">
                  <label htmlFor="drift-hours-input">Forward Drift (hours)</label>
                  <input
                    id="drift-hours-input"
                    type="number"
                    step="any"
                    className="config-text-input"
                    placeholder="Optional"
                    value={driftHours}
                    onChange={(e) => setDriftHours(e.target.value)}
                    disabled={isProcessing || isCompleted}
                  />
                </div>
                <div className="config-form-group">
                  <label htmlFor="lookback-hours-input">Lookback (hours)</label>
                  <input
                    id="lookback-hours-input"
                    type="number"
                    step="any"
                    className="config-text-input"
                    placeholder="Optional"
                    value={lookbackHours}
                    onChange={(e) => setLookbackHours(e.target.value)}
                    disabled={isProcessing || isCompleted}
                  />
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Run Actions */}
        {isCompleted ? (
          <div className="completed-state-box">
            <p className="completed-state-message">
              Investigation already completed. Create a new investigation to re-run the pipeline.
            </p>
            <div className="completed-btn-group">
              <button
                className="primary-button"
                type="button"
                disabled={true}
                style={{ width: '100%' }}
              >
                Pipeline Completed (B1 → F3)
              </button>
              {onOpenCreateModal && (
                <button
                  className="secondary-button create-new-action-btn"
                  type="button"
                  onClick={onOpenCreateModal}
                >
                  <PlusCircle size={14} /> Create New Investigation
                </button>
              )}
            </div>
          </div>
        ) : (
          <div className="run-actions-group">
            <button
              className="primary-button"
              type="button"
              onClick={handleRunClick}
              disabled={isProcessing}
              style={{ width: '100%' }}
              title={
                isProcessing
                  ? 'Pipeline is currently executing...'
                  : isFailed
                    ? 'Retry failed pipeline execution'
                    : 'Run scientific pipeline B1 through F3'
              }
            >
              {isProcessing ? (
                <>
                  <Loader2 size={15} className="spinner" /> Running pipeline (B1 → F3)...
                </>
              ) : isFailed ? (
                <>
                  <Play size={15} fill="currentColor" /> Retry Pipeline (B1 → F3)
                </>
              ) : (
                <>
                  <Play size={15} fill="currentColor" /> Run Investigation (B1 → F3)
                </>
              )}
            </button>

            {activeStatus === 'PROCESSING' && onRefreshStatus && (
              <button
                className="secondary-button refresh-status-button"
                type="button"
                onClick={onRefreshStatus}
                title="Check latest investigation status from backend"
              >
                <RefreshCw size={13} /> Refresh Status
              </button>
            )}
          </div>
        )}

        <div className="layer-controls" style={{ marginTop: '0.75rem' }}>
          <LayerButton label="Calibrated Spill Overlay" icon={Waves} active={layers.spill} onClick={() => onToggleLayer('spill')} />
          <LayerButton label="Drift Trajectories" icon={Activity} active={layers.drift} onClick={() => onToggleLayer('drift')} />
          <LayerButton label="AIS Vessel Tracks" icon={Ship} active={layers.vessels} onClick={() => onToggleLayer('vessels')} />
        </div>
      </section>
    </aside>
  )
}

function LayerButton({
  label,
  icon: Icon,
  active,
  onClick,
}: {
  label: string
  icon: typeof Waves
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      className={`layer-button${active ? ' is-active' : ''}`}
      type="button"
      aria-pressed={active}
      onClick={onClick}
    >
      {active ? <Eye size={14} color="var(--color-accent)" /> : <EyeOff size={14} />}
      <Icon size={14} />
      <span>{label}</span>
    </button>
  )
}