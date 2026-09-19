import { ArrowRight, Compass, Plus, Shield, Sparkles } from 'lucide-react'
import type { InvestigationListItem } from '../../types/investigationApi'
import type { SimulationScenario } from '../../simulation/simulationTypes'
import { incidentData as demoIncident } from '../../data/demoData'

interface OverviewViewProps {
  investigations: InvestigationListItem[]
  activeId: string
  onSelectInvestigation: (id: string) => void
  onNavigateToView: (view: 'workspace' | 'investigations' | 'evidence' | 'vessels' | 'analytics' | 'pipeline' | 'settings') => void
  onOpenCreateModal: () => void
  simulationScenarios: SimulationScenario[]
  isBackendUnavailable?: boolean
}

export function OverviewView({
  investigations,
  activeId,
  onSelectInvestigation,
  onNavigateToView,
  onOpenCreateModal,
  simulationScenarios,
  isBackendUnavailable = false,
}: OverviewViewProps) {
  const totalLive = investigations.length

  const allCases = [
    {
      id: 'corsica-2018-demo',
      name: 'Corsica 2018 Reconstruction',
      type: 'Historical Benchmark',
      badgeClass: 'status-badge--completed',
      status: 'COMPLETED',
      region: 'Cap Corse, Mediterranean Sea',
      description: 'Historical collision reconstruction of ULYSSE and CSL VIRGINIA.',
      image: demoIncident.satelliteImage,
    },
    ...simulationScenarios.map((s) => ({
      id: `simulation-${s.id}`,
      name: s.name,
      type: 'Simulation Showcase',
      badgeClass: 'status-badge--processing',
      status: 'ACTIVE SIMULATION',
      region: s.region,
      description: `Synthetic spill simulation: ${s.spillAreaKm2.toFixed(1)} km² slick in ${s.region}.`,
      image: s.satelliteScene,
    })),
    ...investigations.map((inv) => ({
      id: inv.id,
      name: inv.name,
      type: 'Live Investigation',
      badgeClass: `status-badge--${inv.status.toLowerCase()}`,
      status: inv.status,
      region: 'Area of Interest Registry',
      description: inv.description || 'Active satellite SAR ingestion and attribution investigation.',
      image: demoIncident.satelliteImage,
    })),
  ]

  return (
    <div className="view-container">
      {/* Editorial Hero Section */}
      <div className="view-hero">
        <div className="view-hero-text">
          <span className="view-kicker">Marine Intelligence &amp; Spill Attribution</span>
          <h1 className="view-headline">Investigate the origin of an offshore spill.</h1>
          <p className="view-lead">
            Reconstruct Lagrangian drift trajectories, analyze Sentinel-1 SAR backscatter, and fuse AIS vessel tracking to identify consistent source candidates.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem', flexShrink: 0 }}>
          <button
            className="secondary-button"
            type="button"
            onClick={() => onNavigateToView('workspace')}
          >
            <Compass size={14} /> Open Workspace
          </button>
          <button
            className="primary-button"
            type="button"
            onClick={onOpenCreateModal}
          >
            <Plus size={14} /> New Investigation
          </button>
        </div>
      </div>

      {/* 4 Clean Key Operational Metrics */}
      <div className="telemetry-hero-grid">
        <div className="telemetry-hero-card">
          <span className="telemetry-hero-label">Active Investigations</span>
          <span className="telemetry-hero-value telemetry-hero-value--accent">{totalLive}</span>
          <span className="telemetry-hero-caption">Registered live cases</span>
        </div>

        <div className="telemetry-hero-card">
          <span className="telemetry-hero-label">Simulation Cases</span>
          <span className="telemetry-hero-value telemetry-hero-value--info">{simulationScenarios.length}</span>
          <span className="telemetry-hero-caption">Synthetic scenarios</span>
        </div>

        <div className="telemetry-hero-card">
          <span className="telemetry-hero-label">Historical Cases</span>
          <span className="telemetry-hero-value">1</span>
          <span className="telemetry-hero-caption">Corsica 2018 benchmark</span>
        </div>

        <div className="telemetry-hero-card">
          <span className="telemetry-hero-label">Pipeline Status</span>
          <span className={`telemetry-hero-value ${isBackendUnavailable ? '' : 'telemetry-hero-value--accent'}`} style={{ fontSize: '1.4rem' }}>
            {isBackendUnavailable ? 'Offline' : 'Operational'}
          </span>
          <span className="telemetry-hero-caption">B1 → F3 pipeline ready</span>
        </div>
      </div>

      {/* Visual Investigation Showcase */}
      <section style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
        <div className="section-title-line">
          <div>
            <h2 style={{ fontSize: '1.25rem', fontWeight: 700, margin: 0, color: '#fff' }}>Investigation Showcase</h2>
            <p className="section-caption">Select an active investigation or simulation scenario to launch into the GIS workspace.</p>
          </div>
        </div>

        <div className="catalog-grid">
          {allCases.map((c) => {
            const isSelected = activeId === c.id

            return (
              <article key={c.id} className="catalog-card">
                <img src={c.image} alt={c.name} className="catalog-card-image" />
                <div className="catalog-card-body">
                  <div className="catalog-card-header">
                    <div>
                      <h3 className="catalog-card-title">{c.name}</h3>
                      <span className="catalog-card-region">{c.region}</span>
                    </div>
                    <span className={`status-badge ${c.badgeClass}`}>{c.status}</span>
                  </div>
                  <p className="catalog-card-desc">{c.description}</p>
                  <div className="catalog-card-footer">
                    <span style={{ fontSize: '0.72rem', color: 'var(--color-text-subtle)' }}>{c.type}</span>
                    <button
                      type="button"
                      className={isSelected ? 'primary-button' : 'secondary-button'}
                      style={{ padding: '0.4rem 0.85rem', fontSize: '0.75rem' }}
                      onClick={() => {
                        onSelectInvestigation(c.id)
                        onNavigateToView('workspace')
                      }}
                    >
                      <span>Open Workspace</span>
                      <ArrowRight size={13} />
                    </button>
                  </div>
                </div>
              </article>
            )
          })}
        </div>
      </section>
    </div>
  )
}
