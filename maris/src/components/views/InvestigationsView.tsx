import { useEffect, useState } from 'react'
import { ArrowRight, ChevronDown, ChevronUp, Search } from 'lucide-react'
import type { InvestigationListItem } from '../../types/investigationApi'
import type { SimulationScenario } from '../../simulation/simulationTypes'
import type { MarisView } from '../layout/Header'
import { incidentData as demoIncident } from '../../data/demoData'
import { listExperimentRuns } from '../../real-experiment/experimentApi'

interface InvestigationsViewProps {
  investigations: InvestigationListItem[]
  activeId: string
  onSelectInvestigation: (id: string) => void
  onNavigateToView: (view: MarisView, opts?: { runId?: string; investigationId?: string }) => void
  onOpenCreateModal: () => void
  simulationScenarios: SimulationScenario[]
  isBackendUnavailable?: boolean
}

export function InvestigationsView({
  investigations,
  activeId,
  onSelectInvestigation,
  onNavigateToView,
  onOpenCreateModal,
  simulationScenarios,
  isBackendUnavailable = false,
}: InvestigationsViewProps) {
  const [statusFilter, setStatusFilter] = useState<'ALL' | 'REAL' | 'LIVE' | 'SIMULATION' | 'HISTORICAL'>('ALL')
  const [searchQuery, setSearchQuery] = useState('')
  const [expandedCaseId, setExpandedCaseId] = useState<string | null>(null)
  const [realRuns, setRealRuns] = useState<any[]>([])

  useEffect(() => {
    let isMounted = true
    listExperimentRuns(15)
      .then((res) => {
        if (isMounted && res?.runs) {
          setRealRuns(res.runs)
        }
      })
      .catch(() => {})
    return () => {
      isMounted = false
    }
  }, [])

  const allItems = [
    {
      id: 'corsica-2018-demo',
      name: 'Corsica 2018 Reconstruction',
      type: 'HISTORICAL' as const,
      badgeClass: 'status-badge--completed',
      status: 'COMPLETED',
      region: 'Cap Corse, Mediterranean Sea',
      created: '2018-10-07T07:30:00Z',
      bbox: '9.08°E, 42.90°N to 9.75°E, 43.52°N',
      description: 'Historical collision reconstruction of ULYSSE and CSL VIRGINIA.',
      image: demoIncident.satelliteImage,
    },
    ...simulationScenarios.map((sim) => ({
      id: `simulation-${sim.id}`,
      name: sim.name,
      type: 'SIMULATION' as const,
      badgeClass: 'status-badge--processing',
      status: 'SIMULATION',
      region: sim.region,
      created: sim.timestamp,
      bbox: 'Scenario Predefined Bounds',
      description: `Synthetic showcase: ${sim.spillAreaKm2} km² slick, ${sim.candidateVessels.length} candidate vessels.`,
      image: sim.satelliteScene,
    })),
    ...investigations.map((inv) => {
      const aoi = (inv as any).area_of_interest
      return {
        id: inv.id,
        name: inv.name,
        type: 'LIVE' as const,
        badgeClass: `status-badge--${inv.status.toLowerCase()}`,
        status: inv.status,
        region: 'Custom AOI',
        created: inv.created_at || 'Recent',
        bbox: aoi?.kind === 'bbox'
          ? `${aoi.bbox.west.toFixed(2)}°W, ${aoi.bbox.south.toFixed(2)}°S to ${aoi.bbox.east.toFixed(2)}°E, ${aoi.bbox.north.toFixed(2)}°N`
          : 'Monitored AOI',
        description: inv.description || 'Live Sentinel-1 spill investigation.',
        image: demoIncident.satelliteImage,
      }
    }),
    ...realRuns.map((run) => ({
      id: run.run_id,
      name: `Authoritative Run: ${run.run_id?.slice(0, 16)}`,
      type: 'REAL' as const,
      badgeClass: 'status-badge--completed',
      status: run.status || 'COMPLETED',
      region: run.observation?.platform ? `${run.observation.platform} Sentinel-1 Scene` : 'Mediterranean Sea AOI',
      created: run.created_at || 'Recent',
      bbox: run.observation?.center ? `${run.observation.center[1]?.toFixed(2)}°E, ${run.observation.center[0]?.toFixed(2)}°N` : 'Authoritative Bounds',
      description: `Authoritative persistent run from real_experiments.db. Evaluated ${run.candidate_count ?? 0} vessels from ais_vessels.db.`,
      image: demoIncident.satelliteImage,
    })),
  ]

  const filteredItems = allItems.filter((item) => {
    const q = searchQuery.trim().toLowerCase()
    const matchesSearch =
      !q ||
      (item.name && item.name.toLowerCase().includes(q)) ||
      (item.region && item.region.toLowerCase().includes(q)) ||
      (item.id && item.id.toLowerCase().includes(q))

    if (!matchesSearch) return false

    if (statusFilter === 'ALL') return true
    return item.type === statusFilter
  })

  function handleSelectAndOpen(id: string, type?: string) {
    if (type === 'REAL') {
      onNavigateToView('evaluator', { runId: id })
      return
    }
    onSelectInvestigation(id)
    onNavigateToView('workspace')
  }

  return (
    <div className="view-container">
      {/* Editorial Header */}
      <div className="view-hero">
        <div className="view-hero-text">
          <span className="view-kicker">Investigations Directory</span>
          <h1 className="view-headline">Case Catalog</h1>
          <p className="view-lead">
            Access active investigations, synthetic scenario benchmarks, and historical incident reconstructions.
          </p>
        </div>
      </div>

      {/* 4 Clean Key Operational Metrics (Consolidated from Overview) */}
      <div className="telemetry-hero-grid" style={{ marginBottom: '1.25rem' }}>
        <div className="telemetry-hero-card">
          <span className="telemetry-hero-label">Active Investigations</span>
          <span className="telemetry-hero-value telemetry-hero-value--accent">{investigations.length}</span>
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

      <>
          {/* Filter and Search Bar */}
          <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap' }}>
            <div style={{ position: 'relative', flex: 1, minWidth: '16rem', maxWidth: '420px' }}>
              <Search size={14} style={{ position: 'absolute', left: '0.85rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--color-text-subtle)' }} />
              <input
                type="text"
                placeholder="Search by case name or region..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{
                  width: '100%',
                  padding: '0.55rem 0.85rem 0.55rem 2.2rem',
                  borderRadius: 'var(--radius-sm)',
                  background: 'var(--color-surface)',
                  border: '1px solid var(--color-border)',
                  color: '#fff',
                  fontSize: '0.82rem',
                }}
              />
            </div>

            {/* Filter Pills */}
            <div style={{ display: 'flex', gap: '0.35rem' }}>
              {(['ALL', 'REAL', 'LIVE', 'SIMULATION', 'HISTORICAL'] as const).map((filter) => (
                <button
                  key={filter}
                  type="button"
                  className={statusFilter === filter ? 'primary-button' : 'secondary-button'}
                  style={{ padding: '0.35rem 0.75rem', fontSize: '0.72rem' }}
                  onClick={() => setStatusFilter(filter)}
                >
                  {filter === 'ALL' ? 'All Cases' : filter === 'REAL' ? 'Real Runs' : filter === 'LIVE' ? 'Live G1' : filter === 'SIMULATION' ? 'Simulation' : 'Historical'}
                </button>
              ))}
            </div>
          </div>

          {/* Visual Catalog Grid */}
          <div className="catalog-grid" style={{ marginTop: '1rem' }}>
            {filteredItems.map((item) => {
              const isSelected = activeId === item.id
              const isExpanded = expandedCaseId === item.id

              return (
                <article key={item.id} className="catalog-card">
                  <img
                    src={item.image}
                    alt={item.name}
                    className="catalog-card-image"
                    onClick={() => handleSelectAndOpen(item.id)}
                    style={{ cursor: 'pointer' }}
                    title="Open investigation in Workspace"
                  />
                  <div className="catalog-card-body">
                    <div className="catalog-card-header">
                      <div
                        onClick={() => handleSelectAndOpen(item.id)}
                        style={{ cursor: 'pointer' }}
                        title="Open investigation in Workspace"
                      >
                        <h3 className="catalog-card-title">{item.name}</h3>
                        <span className="catalog-card-region">{item.region}</span>
                      </div>
                      <span className={`status-badge ${item.badgeClass}`}>{item.status}</span>
                    </div>

                    <p className="catalog-card-desc">{item.description}</p>

                    {/* Progressive Disclosure: Technical Metadata */}
                    <div className="disclosure-block">
                      <button
                        type="button"
                        className="disclosure-trigger"
                        onClick={(e) => {
                          e.stopPropagation()
                          setExpandedCaseId(isExpanded ? null : item.id)
                        }}
                      >
                        <span>{isExpanded ? 'Hide technical details' : 'View technical details'}</span>
                        {isExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                      </button>

                      {isExpanded && (
                        <div className="disclosure-content">
                          <dl className="info-list">
                            <div className="info-row">
                              <dt>Case ID</dt>
                              <dd className="mono-num">{item.id}</dd>
                            </div>
                            <div className="info-row">
                              <dt>Coordinates</dt>
                              <dd className="mono-num">{item.bbox}</dd>
                            </div>
                            <div className="info-row">
                              <dt>Timestamp</dt>
                              <dd className="mono-num">{item.created}</dd>
                            </div>
                          </dl>
                        </div>
                      )}
                    </div>

                    <div className="catalog-card-footer">
                      <span style={{ fontSize: '0.72rem', color: 'var(--color-text-subtle)' }}>{item.type}</span>
                      <button
                        type="button"
                        className={isSelected ? 'primary-button' : 'secondary-button'}
                        style={{ padding: '0.4rem 0.85rem', fontSize: '0.75rem' }}
                        onClick={() => handleSelectAndOpen(item.id, item.type)}
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

          {filteredItems.length === 0 && (
            <div style={{ padding: '3.5rem 1rem', textAlign: 'center', color: 'var(--color-text-muted)' }}>
              <p style={{ margin: '0 0 0.75rem', fontSize: '0.85rem' }}>No investigations match your search or filter criteria.</p>
              <button
                type="button"
                className="secondary-button"
                onClick={() => {
                  setSearchQuery('')
                  setStatusFilter('ALL')
                }}
              >
                Reset Filters
              </button>
            </div>
          )}
      </>
    </div>
  )
}
