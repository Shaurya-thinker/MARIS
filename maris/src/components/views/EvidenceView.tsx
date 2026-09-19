import { useState } from 'react'
import { ChevronDown, ChevronUp, Compass, FileText, Satellite, Waves, Wind } from 'lucide-react'
import type { ArtifactSummary } from '../../types/investigationApi'
import type { IncidentData } from '../../types/maris'
import type { SimulationScenario } from '../../simulation/simulationTypes'

interface EvidenceViewProps {
  isDemoMode: boolean
  isSimulationMode: boolean
  simulationScenario: SimulationScenario | null
  demoIncident: IncidentData
  artifacts: ArtifactSummary[]
  activeId: string
  onNavigateToView: (view: 'workspace') => void
}

export function EvidenceView({
  isDemoMode,
  isSimulationMode,
  simulationScenario,
  demoIncident,
  artifacts,
  activeId,
  onNavigateToView,
}: EvidenceViewProps) {
  const [showAcquisitionDetails, setShowAcquisitionDetails] = useState(false)
  const [showMetoceanDetails, setShowMetoceanDetails] = useState(false)

  const currentImage = isDemoMode
    ? demoIncident.satelliteImage
    : isSimulationMode && simulationScenario
      ? simulationScenario.satelliteScene
      : demoIncident.satelliteImage

  const caseName = isDemoMode
    ? 'Corsica 2018 Historical Reconstruction'
    : isSimulationMode && simulationScenario
      ? simulationScenario.name
      : activeId || 'Active Investigation'

  return (
    <div className="view-container">
      {/* Editorial Header */}
      <div className="view-hero">
        <div className="view-hero-text">
          <span className="view-kicker">Sensor Intelligence</span>
          <h1 className="view-headline">Satellite &amp; Environmental Evidence</h1>
          <p className="view-lead">
            High-resolution SAR backscatter scenes, metocean forcing fields, and registered pipeline artifacts for <strong>{caseName}</strong>.
          </p>
        </div>
        <button className="secondary-button" type="button" onClick={() => onNavigateToView('workspace')}>
          <Compass size={14} /> Back to GIS Workspace
        </button>
      </div>

      {/* Dominant Satellite Imagery Section */}
      <section style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div className="section-title-line">
          <div>
            <span className="section-kicker">Sentinel-1 Synthetic Aperture Radar</span>
            <h2 style={{ fontSize: '1.35rem', fontWeight: 700, margin: 0, color: '#fff' }}>Satellite Evidence</h2>
          </div>
          <span className="status-badge status-badge--completed">Calibrated SAR Scene</span>
        </div>

        <div className="satellite-frame" style={{ maxHeight: '520px' }}>
          <img
            src={currentImage}
            alt="Sentinel-1 SAR scene visualization"
            className="satellite-evidence"
            style={{ maxHeight: '520px', width: '100%', objectFit: 'cover' }}
          />
          <span className="satellite-overlay-tag">
            {isDemoMode ? 'S1A_IW_GRDH_1SDV_20181007T052819' : 'COPERNICUS SENTINEL-1 C-BAND SAR'}
          </span>
        </div>

        {/* 3-4 Hero Facts */}
        <div className="telemetry-hero-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Sensor Platform</span>
            <span className="telemetry-hero-value" style={{ fontSize: '1.25rem' }}>Sentinel-1A</span>
            <span className="telemetry-hero-caption">C-band SAR (5.405 GHz)</span>
          </div>

          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Acquired UTC</span>
            <span className="telemetry-hero-value mono-num" style={{ fontSize: '1.15rem' }}>
              {isDemoMode ? '08 Oct 2018 · 05:28' : isSimulationMode ? '12 Mar 2025 · 04:15' : 'Real-time Pass'}
            </span>
            <span className="telemetry-hero-caption">Ascending orbit pass</span>
          </div>

          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Spatial Resolution</span>
            <span className="telemetry-hero-value" style={{ fontSize: '1.25rem' }}>10m × 10m</span>
            <span className="telemetry-hero-caption">Interferometric Wide (IW)</span>
          </div>

          <div className="telemetry-hero-card">
            <span className="telemetry-hero-label">Slick Classification</span>
            <span className="telemetry-hero-value telemetry-hero-value--accent" style={{ fontSize: '1.25rem' }}>Confirmed</span>
            <span className="telemetry-hero-caption">High-confidence dark patch</span>
          </div>
        </div>

        {/* Progressive Disclosure: Acquisition Details */}
        <div className="disclosure-block">
          <button
            type="button"
            className="disclosure-trigger"
            onClick={() => setShowAcquisitionDetails(!showAcquisitionDetails)}
          >
            <span>{showAcquisitionDetails ? 'Hide acquisition details' : 'View acquisition details'}</span>
            {showAcquisitionDetails ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>

          {showAcquisitionDetails && (
            <div className="disclosure-content">
              <div className="metric-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
                <div className="metric">
                  <span>Polarisation Channels</span>
                  <strong>VV + VH Co-polar</strong>
                </div>
                <div className="metric">
                  <span>Processing Level</span>
                  <strong>Level-1 GRD Calibrated</strong>
                </div>
                <div className="metric">
                  <span>Incidence Angle</span>
                  <strong>38.4° – 43.1°</strong>
                </div>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* Environmental & Metocean Forcing Summary */}
      <section style={{ display: 'flex', flexDirection: 'column', gap: '1rem', paddingTop: '1.5rem', borderTop: '1px solid var(--color-border-subtle)' }}>
        <div className="section-title-line">
          <div>
            <span className="section-kicker">Hydrodynamic &amp; Atmospheric Forcing</span>
            <h2 style={{ fontSize: '1.35rem', fontWeight: 700, margin: 0, color: '#fff' }}>Metocean Environment</h2>
          </div>
          <span className="status-badge status-badge--completed">ERA5 &amp; CMEMS</span>
        </div>

        <div className="telemetry-hero-grid" style={{ gridTemplateColumns: 'repeat(2, 1fr)' }}>
          <div className="telemetry-hero-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span className="telemetry-hero-label">Wind Velocity &amp; Vector</span>
              <Wind size={16} color="var(--color-accent)" />
            </div>
            <span className="telemetry-hero-value telemetry-hero-value--accent">14.2 kn SW</span>
            <span className="telemetry-hero-caption">ECMWF ERA5 10m wind reanalysis</span>
          </div>

          <div className="telemetry-hero-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span className="telemetry-hero-label">Ocean Current Field</span>
              <Waves size={16} color="var(--color-info)" />
            </div>
            <span className="telemetry-hero-value telemetry-hero-value--info">0.45 kn NE</span>
            <span className="telemetry-hero-caption">Copernicus Marine (CMEMS) surface velocity</span>
          </div>
        </div>

        {/* Progressive Disclosure: Metocean Details */}
        <div className="disclosure-block">
          <button
            type="button"
            className="disclosure-trigger"
            onClick={() => setShowMetoceanDetails(!showMetoceanDetails)}
          >
            <span>{showMetoceanDetails ? 'Hide metocean details' : 'View metocean details'}</span>
            {showMetoceanDetails ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>

          {showMetoceanDetails && (
            <div className="disclosure-content">
              <div className="metric-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                <div className="metric">
                  <span>Wave Stokes Drift</span>
                  <strong>0.12 kn @ 225°</strong>
                </div>
                <div className="metric">
                  <span>Sea Surface Temp</span>
                  <strong>21.8°C (71.2°F)</strong>
                </div>
                <div className="metric">
                  <span>Significant Wave Height</span>
                  <strong>1.4m (Swell 6.2s)</strong>
                </div>
                <div className="metric">
                  <span>Drift Leeway Factor</span>
                  <strong>3.2% Wind + 100% Current</strong>
                </div>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* Registered Pipeline Artifacts */}
      {artifacts.length > 0 && (
        <section style={{ display: 'flex', flexDirection: 'column', gap: '1rem', paddingTop: '1.5rem', borderTop: '1px solid var(--color-border-subtle)' }}>
          <div className="section-title-line">
            <div>
              <span className="section-kicker">Registered Artifacts ({artifacts.length})</span>
              <h2 style={{ fontSize: '1.2rem', fontWeight: 700, margin: 0, color: '#fff' }}>Asset Registry</h2>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {artifacts.map((art) => (
              <div
                key={art.asset_id}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '0.75rem 1rem',
                  borderRadius: 'var(--radius-sm)',
                  background: 'var(--color-surface)',
                  border: '1px solid var(--color-border)',
                }}
              >
                <div>
                  <strong style={{ color: '#fff', fontSize: '0.85rem' }}>{art.asset_type}</strong>
                  <span style={{ display: 'block', fontSize: '0.72rem', color: 'var(--color-text-subtle)', fontFamily: 'var(--font-mono)' }}>
                    {art.location}
                  </span>
                </div>
                <span className="vessel-tag">{art.provider}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
