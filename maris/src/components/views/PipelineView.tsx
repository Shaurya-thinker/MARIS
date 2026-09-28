import { useEffect, useState, useMemo } from 'react'
import { CheckCircle2, ChevronDown, ChevronUp, Circle, Clock, Compass, Database, Loader2, Navigation, Zap } from 'lucide-react'
import { listExperimentRuns, getExperimentRun } from '../../real-experiment/experimentApi'
import type { ExperimentRunResult } from '../../real-experiment/experimentTypes'

interface PipelineViewProps {
  completedStages?: string[]
  currentStage?: string | null
  workflowStatus?: string
  onNavigateToView: (view: any) => void
  onRunWorkflow?: () => void
  isRunningWorkflow?: boolean
}

// ── Pipeline stage definitions — keyed to the REAL Evaluator workflow ────────
// Status is derived from actual ExperimentRunResult fields, NOT from fake props

interface Stage {
  id: string
  code: string
  name: string
  category: string
  categoryColor: string
  description: string
  /** Field path on ExperimentRunResult that signals this stage completed */
  completionCheck: (run: ExperimentRunResult) => boolean
  /** Extract a real value from the run to show as artifact detail */
  artifactDetail?: (run: ExperimentRunResult) => string | null
  input: string
  output: string
}

const PIPELINE_STAGES: Stage[] = [
  {
    id: 'S1', code: 'S1', name: 'Sentinel-1 Product Discovery',
    category: 'SAR Acquisition', categoryColor: '#60a5fa',
    description: 'Queries Copernicus CDSE catalogue for Sentinel-1 GRD scenes intersecting the AOI bounding box. Returns product metadata without downloading data.',
    completionCheck: (r) => Boolean(r.satellite_product_id),
    artifactDetail: (r) => r.satellite_product_id ? `Product ID: ${r.satellite_product_id}` : null,
    input: 'AOI bounding box + time window',
    output: 'S1 Product ID + sensing time',
  },
  {
    id: 'S2', code: 'S2', name: 'Slick Detection & Characterization',
    category: 'SAR Processing', categoryColor: '#60a5fa',
    description: 'Adaptive thresholding and morphological segmentation on calibrated sigma0 backscatter to identify dark ocean surface slick polygons and extract centroid, area, and confidence.',
    completionCheck: (r) => Boolean(r.slick_characterization),
    artifactDetail: (r) => {
      const s = r.slick_characterization
      if (!s) return null
      const area = s.area_km2 != null ? `${s.area_km2.toFixed(2)} km²` : '?'
      const conf = s.confidence != null ? ` · ${(s.confidence * 100).toFixed(0)}% confidence` : ''
      return `Slick area: ${area}${conf}`
    },
    input: 'Sentinel-1 Product ID',
    output: 'spill_geometry (centroid + polygon + area_km2)',
  },
  {
    id: 'C1', code: 'C1', name: 'ERA5 Wind Field Acquisition',
    category: 'Metocean', categoryColor: '#a78bfa',
    description: 'Downloads ECMWF ERA5 reanalysis 10m wind vectors (u, v components) for the investigation time window from Copernicus Climate Data Store.',
    completionCheck: (r) => Boolean(r.era5_path),
    artifactDetail: (r) => r.era5_path ? `Path: …${r.era5_path.split('/').slice(-2).join('/')}` : null,
    input: 'Observation time + spatial extent',
    output: 'ERA5_wind_uv_grid.nc (NetCDF)',
  },
  {
    id: 'C2', code: 'C2', name: 'CMEMS Current Field Acquisition',
    category: 'Metocean', categoryColor: '#a78bfa',
    description: 'Downloads Copernicus Marine (CMEMS) surface ocean current vectors (u, v components) for the investigation spatial-temporal window.',
    completionCheck: (r) => Boolean(r.cmems_path),
    artifactDetail: (r) => r.cmems_path ? `Path: …${r.cmems_path.split('/').slice(-2).join('/')}` : null,
    input: 'Observation time + spatial extent',
    output: 'CMEMS_current_uv_grid.nc (NetCDF)',
  },
  {
    id: 'D1', code: 'D1', name: 'Backward Drift Integration',
    category: 'Drift Model', categoryColor: '#f59e0b',
    description: 'Runs Runge-Kutta 4th-order Lagrangian advection backward in time from the observed slick centroid through the ERA5+CMEMS vector fields to reconstruct the probable spill origin zone.',
    completionCheck: (r) => Array.isArray(r.backward_steps) && r.backward_steps.length > 0,
    artifactDetail: (r) => {
      if (!Array.isArray(r.backward_steps) || !r.backward_steps.length) return null
      return `${r.backward_steps.length} integration steps · ${r.backtrack_hours}h backtrack · ${r.step_hours}h step`
    },
    input: 'spill_geometry + ERA5/CMEMS grids',
    output: 'backward_steps[] (lon, lat, timestamp, UV per step)',
  },
  {
    id: 'D2', code: 'D2', name: 'Source Zone Estimation',
    category: 'Drift Model', categoryColor: '#f59e0b',
    description: 'Derives the source candidate zone: a circular probability region centred at the final backward drift position, sized by the accumulated Gaussian particle spread.',
    completionCheck: (r) => r.source_lon != null && r.source_lat != null && r.source_radius_m > 0,
    artifactDetail: (r) => {
      if (r.source_lon == null) return null
      return `Origin: ${r.source_lat?.toFixed(4)}°N, ${r.source_lon?.toFixed(4)}°E · radius ${(r.source_radius_m / 1000).toFixed(1)} km`
    },
    input: 'backward_steps[] (final step)',
    output: 'source_candidate_zone (GeoJSON circle)',
  },
  {
    id: 'D3', code: 'D3', name: 'Forward Drift Cross-Check',
    category: 'Drift Model', categoryColor: '#f59e0b',
    description: 'Optional forward drift simulation from the reconstructed origin as a non-additive physical consistency cross-check. Does not contribute numerically to the Evidence Consistency Score.',
    completionCheck: (r) => Boolean(r.forward_prediction),
    artifactDetail: (r) => {
      const fp = r.forward_prediction
      if (!fp) return null
      return `${fp.prediction_hours}h forward · final: ${fp.final_lat?.toFixed(4)}°N, ${fp.final_lon?.toFixed(4)}°E · ${fp.displacement_km?.toFixed(2)} km`
    },
    input: 'source_candidate_zone + ERA5/CMEMS grids',
    output: 'forward_prediction (displacement_km, steps[])',
  },
  {
    id: 'E1', code: 'E1', name: 'AIS Vessel Query',
    category: 'AIS Correlation', categoryColor: '#34d399',
    description: 'Queries ais_vessels.db for all vessel AIS positions intersecting the source candidate zone spatial-temporal window during the estimated spill release period.',
    completionCheck: (r) => Array.isArray(r.vessels) && r.vessels.length > 0,
    artifactDetail: (r) => {
      if (!Array.isArray(r.vessels)) return null
      return `${r.vessels.length} candidate vessels identified from ais_vessels.db`
    },
    input: 'source_candidate_zone + time window',
    output: 'Candidate_Vessel_Registry (VesselFeatures[])',
  },
  {
    id: 'E2', code: 'E2', name: 'Evidence Scoring & Ranking',
    category: 'Attribution', categoryColor: '#00c896',
    description: 'Computes the multi-channel Evidence Consistency Score (ECS) for each candidate: 40% Spatial × proximity to origin zone, 35% Temporal × time window overlap, 25% Trajectory × heading vs drift axis. Ranks by ECS descending.',
    completionCheck: (r) => Array.isArray(r.vessels) && r.vessels.some((v: any) => v.evidence_consistency_score != null),
    artifactDetail: (r) => {
      if (!Array.isArray(r.vessels) || !r.vessels.length) return null
      const top = r.vessels.find((v: any) => v.rank === 1) as any
      if (!top) return null
      const ecs = top.evidence_consistency_score != null ? `${Math.round(top.evidence_consistency_score * 100)}%` : '?'
      return `Top candidate: ${top.vessel_name ?? top.mmsi ?? 'Unknown'} · ECS = ${ecs}`
    },
    input: 'Candidate_Vessel_Registry + source_candidate_zone',
    output: 'ranked_vessels[] (ECS scores, rank, has_meaningful_support)',
  },
  {
    id: 'E3', code: 'E3', name: 'Behavioral Intelligence (Non-Additive)',
    category: 'Attribution', categoryColor: '#00c896',
    description: 'Rule-based contextual analysis identifying AIS transmission gaps, speed anomalies, loitering, and course deviations. Contributes 0.00 numerically to ECS — strictly contextual observation layer.',
    completionCheck: (r) => Array.isArray(r.vessels) && r.vessels.some((v: any) => v.behavioral_intelligence != null),
    artifactDetail: (r) => {
      if (!Array.isArray(r.vessels)) return null
      const withBI = r.vessels.filter((v: any) => v.behavioral_intelligence != null).length
      return withBI > 0 ? `${withBI} of ${r.vessels.length} vessels with behavioral analysis` : null
    },
    input: 'Candidate_Vessel_Registry (AIS position sequences)',
    output: 'behavioral_intelligence per vessel (non-additive)',
  },
  {
    id: 'F1', code: 'F1', name: 'Scientific Report Generation',
    category: 'Report', categoryColor: '#f87171',
    description: 'Generates the complete authoritative investigation record: ECS breakdown per channel per vessel, mandatory scientific disclaimers, provenance chain, and export-ready JSON + PDF artifacts.',
    completionCheck: (r) => Boolean(r.model_version) && Array.isArray(r.vessels) && r.vessels.length > 0,
    artifactDetail: (r) => r.created_at
      ? `Run completed: ${new Date(r.created_at).toLocaleString()} · model ${r.model_version}`
      : null,
    input: 'ranked_vessels[] + behavioral_intelligence',
    output: 'MARIS_Scientific_Report.pdf + export.json (real_experiments.db)',
  },
]

// ── Color per category ────────────────────────────────────────────────────────
function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

export function PipelineView({
  onNavigateToView,
}: PipelineViewProps) {
  const [selectedStageId, setSelectedStageId] = useState<string | null>(null)
  const [run, setRun] = useState<ExperimentRunResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    setLoading(true)
    listExperimentRuns(1)
      .then((res) => {
        if (!mounted || !res?.runs?.length) { if (mounted) setLoading(false); return }
        return getExperimentRun(res.runs[0].run_id)
      })
      .then((r) => { if (mounted && r) setRun(r) })
      .catch((e) => { if (mounted) setError(e?.message ?? 'Failed to load') })
      .finally(() => { if (mounted) setLoading(false) })
    return () => { mounted = false }
  }, [])

  // Derive per-stage status from the real run
  const stageStatuses = useMemo(() => {
    if (!run) return {}
    const map: Record<string, 'COMPLETED' | 'SKIPPED' | 'PENDING'> = {}
    for (const stage of PIPELINE_STAGES) {
      try {
        map[stage.id] = stage.completionCheck(run) ? 'COMPLETED' : 'PENDING'
      } catch {
        map[stage.id] = 'PENDING'
      }
    }
    // D3 (forward prediction) is optional — mark as SKIPPED if not present
    if (map['D3'] === 'PENDING' && run.vessels?.length > 0) map['D3'] = 'SKIPPED'
    // E3 (behavioral) is optional
    if (map['E3'] === 'PENDING' && run.vessels?.length > 0) map['E3'] = 'SKIPPED'
    return map
  }, [run])

  const completedCount = Object.values(stageStatuses).filter((s) => s === 'COMPLETED').length
  const totalStages = PIPELINE_STAGES.length

  return (
    <div className="view-container">
      {/* Header */}
      <div className="view-hero">
        <div className="view-hero-text">
          <span className="view-kicker">Scientific Pipeline Architecture</span>
          <h1 className="view-headline">MARIS Attribution Pipeline</h1>
          <p className="view-lead">
            End-to-end execution trace: Copernicus SAR acquisition → Lagrangian drift backtracking → AIS correlation → multi-channel evidence scoring.
            {run && (
              <span className="re-badge" style={{ marginLeft: '0.65rem', fontSize: '0.72rem' }}>
                Latest run: {run.run_id.slice(0, 16)} · {fmtDate(run.created_at)}
              </span>
            )}
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.65rem', flexWrap: 'wrap' }}>
          <button className="secondary-button" type="button" onClick={() => onNavigateToView('workspace')}>
            <Compass size={14} /> GIS Workspace
          </button>
          <button className="primary-button" type="button" onClick={() => onNavigateToView('evaluator')}>
            <Zap size={14} /> Launch Evaluator →
          </button>
        </div>
      </div>

      {/* Loading / error */}
      {loading && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.82rem', color: 'var(--color-text-subtle)', marginBottom: '1rem' }}>
          <Loader2 size={14} className="spinner" /> Loading latest run…
        </div>
      )}
      {error && (
        <div style={{ padding: '0.6rem 1rem', borderRadius: 'var(--radius-xs)', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.22)', color: 'rgba(239,68,68,0.85)', fontSize: '0.78rem', marginBottom: '1rem' }}>
          {error}
        </div>
      )}

      {/* Run summary hero — only when data is available */}
      {run && (
        <div className="telemetry-hero-grid" style={{ marginBottom: '1.5rem' }}>
          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Pipeline Completion</span>
            <span className="telemetry-hero-value telemetry-hero-value--accent">
              {completedCount} / {totalStages}
            </span>
            <span className="telemetry-hero-caption">stages completed in latest run</span>
          </div>
          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Candidates Evaluated</span>
            <span className="telemetry-hero-value telemetry-hero-value--info">
              {run.vessels?.length ?? 0}
            </span>
            <span className="telemetry-hero-caption">vessels from ais_vessels.db</span>
          </div>
          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Drift Steps</span>
            <span className="telemetry-hero-value">
              {Array.isArray(run.backward_steps) ? run.backward_steps.length : '—'}
            </span>
            <span className="telemetry-hero-caption">{run.backtrack_hours}h backtrack · {run.step_hours}h step</span>
          </div>
          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Model Version</span>
            <span className="telemetry-hero-value" style={{ fontSize: '1.1rem', fontFamily: 'var(--font-mono)' }}>
              {run.model_version ?? '—'}
            </span>
            <span className="telemetry-hero-caption">Drift + attribution engine</span>
          </div>
        </div>
      )}

      {/* No-run placeholder */}
      {!loading && !run && !error && (
        <div style={{
          padding: '2.5rem 1.5rem', textAlign: 'center',
          borderRadius: 'var(--radius-sm)', border: '1px dashed var(--color-border)',
          color: 'var(--color-text-muted)', marginBottom: '1.5rem',
        }}>
          <Database size={24} style={{ opacity: 0.3, marginBottom: '0.65rem' }} />
          <p style={{ margin: '0 0 0.75rem', fontSize: '0.85rem' }}>No experiment runs found in <code style={{ fontFamily: 'var(--font-mono)' }}>real_experiments.db</code>.</p>
          <button type="button" className="primary-button" onClick={() => onNavigateToView('evaluator')}>
            <Zap size={14} /> Run your first investigation in Evaluator →
          </button>
        </div>
      )}

      {/* Pipeline Timeline */}
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        {PIPELINE_STAGES.map((stage, idx) => {
          const status = run ? (stageStatuses[stage.id] ?? 'PENDING') : 'PENDING'
          const isCompleted = status === 'COMPLETED'
          const isSkipped = status === 'SKIPPED'
          const isExpanded = selectedStageId === stage.id
          const isLast = idx === PIPELINE_STAGES.length - 1
          const artifact = run && isCompleted ? stage.artifactDetail?.(run) : null

          return (
            <div key={stage.id} style={{ display: 'flex', gap: '0', position: 'relative' }}>
              {/* Connector column */}
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: 48, flexShrink: 0 }}>
                {/* Node circle */}
                <div style={{
                  width: 36, height: 36, borderRadius: '50%', flexShrink: 0,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: isCompleted
                    ? 'rgba(0,200,150,0.12)'
                    : isSkipped
                      ? 'rgba(148,163,184,0.1)'
                      : 'var(--color-surface)',
                  border: `2px solid ${isCompleted ? 'var(--color-accent)' : isSkipped ? 'var(--color-text-subtle)' : 'var(--color-border)'}`,
                  zIndex: 1,
                  transition: 'all var(--transition-fast)',
                }}>
                  {isCompleted
                    ? <CheckCircle2 size={16} color="var(--color-accent)" />
                    : isSkipped
                      ? <Circle size={12} color="var(--color-text-subtle)" />
                      : <span style={{ fontSize: '0.65rem', fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--color-text-subtle)' }}>{stage.code}</span>
                  }
                </div>
                {/* Vertical connector line — only between stages, not after last */}
                {!isLast && (
                  <div style={{
                    width: 2, flex: 1, minHeight: 16,
                    background: isCompleted
                      ? 'linear-gradient(180deg, var(--color-accent) 0%, rgba(0,200,150,0.2) 100%)'
                      : 'var(--color-border)',
                    margin: '2px 0',
                    transition: 'background 0.3s ease',
                  }} />
                )}
              </div>

              {/* Stage content card */}
              <div
                style={{
                  flex: 1, marginBottom: isLast ? 0 : 12,
                  borderRadius: 'var(--radius-sm)',
                  background: isCompleted ? 'rgba(0,200,150,0.04)' : 'var(--color-surface)',
                  border: `1px solid ${isCompleted ? 'rgba(0,200,150,0.2)' : isExpanded ? stage.categoryColor + '33' : 'var(--color-border)'}`,
                  cursor: 'pointer',
                  transition: 'all var(--transition-fast)',
                  marginLeft: 12,
                }}
                onClick={() => setSelectedStageId(isExpanded ? null : stage.id)}
              >
                {/* Card header row */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0.75rem 1rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
                    {/* Category pill */}
                    <span style={{
                      fontSize: '0.62rem', padding: '0.12rem 0.5rem', borderRadius: 99,
                      background: `${stage.categoryColor}18`, border: `1px solid ${stage.categoryColor}44`,
                      color: stage.categoryColor, fontWeight: 600, whiteSpace: 'nowrap',
                    }}>
                      {stage.category}
                    </span>
                    <strong style={{ fontSize: '0.9rem', color: isCompleted ? '#fff' : 'var(--color-text-muted)' }}>
                      {stage.name}
                    </strong>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    {/* Status badge */}
                    <span className={`status-badge ${isCompleted ? 'status-badge--completed' : isSkipped ? '' : 'status-badge--created'}`}
                      style={isSkipped ? { background: 'rgba(148,163,184,0.1)', color: 'var(--color-text-subtle)', border: '1px solid var(--color-border)' } : {}}>
                      {isCompleted ? 'COMPLETED' : isSkipped ? 'OPTIONAL / SKIPPED' : 'NOT YET RUN'}
                    </span>
                    {isExpanded ? <ChevronUp size={13} color="var(--color-text-subtle)" /> : <ChevronDown size={13} color="var(--color-text-subtle)" />}
                  </div>
                </div>

                {/* Description always visible */}
                <div style={{ padding: '0 1rem 0.65rem', borderTop: '1px solid var(--color-border-subtle)' }}>
                  <p style={{ fontSize: '0.77rem', color: 'var(--color-text-muted)', margin: '0.5rem 0 0', lineHeight: 1.5 }}>
                    {stage.description}
                  </p>

                  {/* Real artifact value — shown when completed */}
                  {artifact && (
                    <div style={{
                      marginTop: '0.5rem', padding: '0.35rem 0.65rem', borderRadius: 'var(--radius-xs)',
                      background: 'rgba(0,200,150,0.07)', border: '1px solid rgba(0,200,150,0.2)',
                      fontSize: '0.72rem', fontFamily: 'var(--font-mono)', color: 'var(--color-accent)',
                    }}>
                      ✓ {artifact}
                    </div>
                  )}
                </div>

                {/* Expanded detail panel */}
                {isExpanded && (
                  <div style={{
                    margin: '0 1rem 0.75rem', paddingTop: '0.75rem',
                    borderTop: '1px solid var(--color-border-subtle)',
                    display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.65rem',
                  }}>
                    <div className="metric">
                      <span>Input</span>
                      <strong style={{ fontSize: '0.7rem', fontFamily: 'var(--font-mono)' }}>{stage.input}</strong>
                    </div>
                    <div className="metric">
                      <span>Output</span>
                      <strong className="metric--accent" style={{ fontSize: '0.7rem', fontFamily: 'var(--font-mono)' }}>{stage.output}</strong>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
