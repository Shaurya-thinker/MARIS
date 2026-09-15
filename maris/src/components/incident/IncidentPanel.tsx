import { Activity, AlertTriangle, Eye, EyeOff, Loader2, Play, Satellite, Ship, Waves } from 'lucide-react'
import type { IncidentData } from '../../types/maris'
import type {
  ArtifactSummary,
  InvestigationResponse,
  InvestigationStatusResponse,
} from '../../types/investigationApi'

interface IncidentPanelProps {
  isDemoMode: boolean
  demoIncident: IncidentData
  liveInvestigation: InvestigationResponse | null
  statusResponse: InvestigationStatusResponse | null
  artifacts: ArtifactSummary[]
  layers: { spill: boolean; drift: boolean; vessels: boolean }
  onToggleLayer: (layer: 'spill' | 'drift' | 'vessels') => void
  onRunWorkflow: () => void
  isRunningWorkflow: boolean
  error?: string | null
}

function InfoRow({ label, value }: { label: string; value: string | React.ReactNode }) {
  return (
    <div className="info-row">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}

export function IncidentPanel({
  isDemoMode,
  demoIncident,
  liveInvestigation,
  statusResponse,
  artifacts,
  layers,
  onToggleLayer,
  onRunWorkflow,
  isRunningWorkflow,
  error,
}: IncidentPanelProps) {
  // Demo mode: show the historical Corsica demo panel
  if (isDemoMode) {
    return (
      <aside className="panel incident-panel" aria-label="Incident and map controls">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">Case file (Demo)</span>
            <h2>Incident overview</h2>
          </div>
          <Activity size={18} className="heading-icon" aria-hidden="true" />
        </div>

        <section className="panel-section">
          <h3>Incident</h3>
          <dl className="info-list">
            <InfoRow label="Incident ID" value={demoIncident.id} />
            <InfoRow label="Region" value={demoIncident.region} />
            <InfoRow label="Status" value={demoIncident.status} />
            <InfoRow label="Detected" value={demoIncident.detectionTime} />
            <InfoRow label="Updated" value={demoIncident.lastUpdated} />
          </dl>
        </section>

        <section className="panel-section">
          <div className="section-title-line">
            <h3>Satellite scene</h3>
            <Satellite size={15} aria-hidden="true" />
          </div>
          <img
            className="satellite-evidence"
            src={demoIncident.satelliteImage}
            alt="Sentinel-1A SAR visualization of the reconstructed Corsica oil slick"
          />
          <dl className="info-list">
            <InfoRow label="Source" value={demoIncident.satelliteSource} />
            <InfoRow label="Acquired" value={demoIncident.acquisitionTime} />
            <InfoRow label="Scene status" value={demoIncident.sceneStatus} />
          </dl>
        </section>

        <section className="panel-section">
          <div className="section-title-line">
            <h3>Spill</h3>
            <Waves size={15} aria-hidden="true" />
          </div>
          <dl className="info-list">
            <InfoRow label="Status" value="Detected oil spill" />
            <InfoRow label="Est. area" value={demoIncident.estimatedArea} />
            <InfoRow label="Confidence" value={demoIncident.confidence} />
          </dl>
        </section>

        <section className="panel-section controls-section">
          <h3>Controls</h3>
          <div className="layer-controls">
            <LayerButton label="Spill overlay" icon={Waves} active={layers.spill} onClick={() => onToggleLayer('spill')} />
            <LayerButton label="Drift paths" icon={Activity} active={layers.drift} onClick={() => onToggleLayer('drift')} />
            <LayerButton label="Vessel tracks" icon={Ship} active={layers.vessels} onClick={() => onToggleLayer('vessels')} />
          </div>
        </section>

        <div className="demo-note">HISTORICAL DEMO CASE — Corsica 2018 Reconstruction</div>
      </aside>
    )
  }

  // Live mode — investigation not yet loaded (loading or API error)
  if (!liveInvestigation) {
    return (
      <aside className="panel incident-panel" aria-label="Incident and map controls">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">Live Investigation</span>
            <h2>{error ? 'Unavailable' : 'Loading…'}</h2>
          </div>
          <Activity size={18} className="heading-icon" aria-hidden="true" />
        </div>
        {error && (
          <div className="error-callout" role="alert">
            <AlertTriangle size={16} />
            <span>{error}</span>
          </div>
        )}
        {!error && (
          <div className="panel-section">
            <Loader2 size={16} className="spinner" />
            <span style={{ marginLeft: 8, opacity: 0.7 }}>Loading investigation details…</span>
          </div>
        )}
      </aside>
    )
  }

  // Live G1 Investigation View
  const aoi = liveInvestigation.area_of_interest
  const aoiDescription =
    aoi.kind === 'bbox'
      ? `[${aoi.bbox.west.toFixed(2)}°W, ${aoi.bbox.south.toFixed(2)}°S, ${aoi.bbox.east.toFixed(2)}°E, ${aoi.bbox.north.toFixed(2)}°N]`
      : `GeoJSON Polygon (${aoi.polygon.coordinates[0]?.length || 0} pts)`

  // Check for B3 spill artifact
  const spillArtifact = artifacts.find(
    (a) => a.asset_type === 'spill_geometry' || a.source === 'spill_detection'
  )
  const spillMetadata = spillArtifact?.metadata || {}
  const spillCentroid = spillMetadata.centroid as { longitude: number; latitude: number } | undefined
  const spillAreaM2 = (spillMetadata.total_area_m2 || spillMetadata.area) as number | undefined
  const spillConfidence = spillMetadata.confidence as number | undefined

  const activeStatus = statusResponse?.status || liveInvestigation.status
  const errors = statusResponse?.errors || []

  return (
    <aside className="panel incident-panel" aria-label="Incident and map controls">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Live Investigation</span>
          <h2>{liveInvestigation.name}</h2>
        </div>
        <Activity size={18} className="heading-icon" aria-hidden="true" />
      </div>

      {error && (
        <div className="error-callout" role="alert">
          <AlertTriangle size={16} />
          <span>{error}</span>
        </div>
      )}

      {errors.length > 0 && (
        <div className="stage-error-box" role="alert">
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
          <InfoRow label="ID" value={liveInvestigation.id} />
          <InfoRow label="Status" value={<span className={`status-badge status-badge--${activeStatus.toLowerCase()}`}>{activeStatus}</span>} />
          <InfoRow label="AOI" value={aoiDescription} />
          <InfoRow
            label="Time Window"
            value={`${new Date(liveInvestigation.time_window.start).toLocaleDateString()} – ${new Date(liveInvestigation.time_window.end).toLocaleDateString()}`}
          />
          <InfoRow
            label="Created"
            value={new Date(liveInvestigation.created_at).toLocaleString()}
          />
          {liveInvestigation.description && (
            <InfoRow label="Notes" value={liveInvestigation.description} />
          )}
        </dl>
      </section>

      <section className="panel-section">
        <div className="section-title-line">
          <h3>Spill Detection</h3>
          <Waves size={15} aria-hidden="true" />
        </div>
        <dl className="info-list">
          <InfoRow
            label="Status"
            value={
              spillArtifact
                ? spillMetadata.detected
                  ? 'Confirmed slick detected'
                  : 'No slick detected'
                : 'Pending B3 detection'
            }
          />
          {spillAreaM2 !== undefined && (
            <InfoRow
              label="Area"
              value={`${spillAreaM2.toLocaleString()} m² (${(spillAreaM2 / 1_000_000).toFixed(3)} km²)`}
            />
          )}
          {spillConfidence !== undefined && (
            <InfoRow label="Confidence" value={`${(spillConfidence * 100).toFixed(0)}%`} />
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
          <span className="badge-subtle">{artifacts.length} Artifacts</span>
        </div>

        <button
          className="primary-button run-button"
          type="button"
          onClick={onRunWorkflow}
          disabled={isRunningWorkflow || activeStatus === 'PROCESSING'}
        >
          {isRunningWorkflow || activeStatus === 'PROCESSING' ? (
            <>
              <Loader2 size={15} className="spinner" /> Running pipeline (B1 → F3)...
            </>
          ) : (
            <>
              <Play size={15} fill="currentColor" /> Run Investigation (B1 → F3)
            </>
          )}
        </button>

        <div className="layer-controls">
          <LayerButton label="Spill overlay" icon={Waves} active={layers.spill} onClick={() => onToggleLayer('spill')} />
          <LayerButton label="Drift paths" icon={Activity} active={layers.drift} onClick={() => onToggleLayer('drift')} />
          <LayerButton label="Vessel tracks" icon={Ship} active={layers.vessels} onClick={() => onToggleLayer('vessels')} />
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
      {active ? <Eye size={15} /> : <EyeOff size={15} />}
      <Icon size={14} />
      <span>{label}</span>
    </button>
  )
}