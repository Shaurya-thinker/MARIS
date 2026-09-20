import { useEffect, useRef, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Compass,
  Layers,
  Loader2,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  SlidersHorizontal,
} from 'lucide-react'
import { candidateVessels as demoCandidateVessels, incidentData as demoIncidentData, pipelineStages as demoPipelineStages } from '../../data/demoData'
import { createSimulationInvestigation, SIMULATION_STAGE_SEQUENCE } from '../../simulation/simulationEngine'
import type { SimulationScenario } from '../../simulation/simulationTypes'
import { AnalysisPanel } from '../analysis/AnalysisPanel'
import { CreateInvestigationModal } from '../incident/CreateInvestigationModal'
import { IncidentPanel } from '../incident/IncidentPanel'
import { MapView } from '../map/MapView'
import { InvestigationPipeline } from '../pipeline/InvestigationPipeline'
import { InvestigationPlayback } from '../../prototype/InvestigationPlayback'
import { getPrototypePipelineStages, prototypeFlowStages } from '../../prototype/prototypeFlow'
import {
  createInvestigation,
  getCandidateRanking,
  getExplainabilityReport,
  getInvestigation,
  getInvestigationArtifacts,
  getInvestigationStatus,
  runInvestigation,
} from '../../api/investigationApi'
import type {
  ArtifactSummary,
  CandidateRanking,
  ExplainabilityReport,
  InvestigationCreateRequest,
  InvestigationListItem,
  InvestigationResponse,
  InvestigationRunRequest,
  InvestigationStatusResponse,
} from '../../types/investigationApi'

/**
 * Resolves the authoritative spill identifier from registered artifacts.
 * Priority: 1. metadata.detection_id -> 2. provenance.product_id -> 3. asset.id / asset.asset_id
 */
export function resolveSpillId(artifacts: ArtifactSummary[]): string | null {
  if (!Array.isArray(artifacts)) return null
  const spillAsset = artifacts.find(
    (a) => a.asset_type === 'spill_geometry' || a.source === 'spill_detection'
  )
  if (!spillAsset) return null

  const detectionId = spillAsset.metadata?.detection_id
  if (typeof detectionId === 'string' && detectionId.trim().length > 0) {
    return detectionId.trim()
  }

  const productId = spillAsset.provenance?.product_id
  if (typeof productId === 'string' && productId.trim().length > 0) {
    return productId.trim()
  }

  const directId = spillAsset.asset_id || (spillAsset as unknown as { id?: string }).id
  if (typeof directId === 'string' && directId.trim().length > 0) {
    return directId.trim()
  }

  return null
}

export function InvestigationWorkspace({
  activeId,
  isDemoMode,
  isSimulationMode = false,
  simulationScenario = null,
  investigations = [],
  onSelectInvestigation,
  isCreateModalOpen,
  onCloseCreateModal,
  onOpenCreateModal,
  onInvestigationCreated,
  backendError,
  onNavigateToEvaluator,
}: {
  activeId: string
  isDemoMode: boolean
  isSimulationMode?: boolean
  simulationScenario?: SimulationScenario | null
  investigations?: InvestigationListItem[]
  onSelectInvestigation: (id: string) => void
  isCreateModalOpen: boolean
  onCloseCreateModal: () => void
  onOpenCreateModal: () => void
  onInvestigationCreated?: () => Promise<void> | void
  backendError?: string | null
  onNavigateToEvaluator?: () => void
}) {
  const [layers, setLayers] = useState({ spill: true, drift: true, vessels: true })
  const [selectedCandidate, setSelectedCandidate] = useState(demoCandidateVessels[0].id)

  // Floating Mission Drawer States for Fullscreen Map Immersion
  const [isLeftDrawerOpen, setIsLeftDrawerOpen] = useState(true)
  const [isRightDrawerOpen, setIsRightDrawerOpen] = useState(true)

  // Live G1 Investigation Data
  const [activeInvestigation, setActiveInvestigation] = useState<InvestigationResponse | null>(null)
  const [statusResponse, setStatusResponse] = useState<InvestigationStatusResponse | null>(null)
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([])
  const [rankingResult, setRankingResult] = useState<CandidateRanking | null>(null)
  const [explainabilityReport, setExplainabilityReport] = useState<ExplainabilityReport | null>(null)

  // Loading and Error States
  const [isLoading, setIsLoading] = useState(false)
  const [loadingMessage, setLoadingMessage] = useState('Loading...')
  const [isRunningWorkflow, setIsRunningWorkflow] = useState(false)
  const [apiError, setApiError] = useState<string | null>(null)

  // Demo playback flow states
  const [playbackActive, setPlaybackActive] = useState(false)
  const [playbackPlaying, setPlaybackPlaying] = useState(false)
  const [playbackStage, setPlaybackStage] = useState(0)
  const [simulationTimeline, setSimulationTimeline] = useState(SIMULATION_STAGE_SEQUENCE)

  function toggleLayer(layer: 'spill' | 'drift' | 'vessels') {
    setLayers((current) => ({ ...current, [layer]: !current[layer] }))
  }

  const processingTimerRef = useRef<number | null>(null)
  const [hasDeferredChecked, setHasDeferredChecked] = useState(false)

  const currentInvestigationItem = investigations.find((i) => i.id === activeId)
  const currentStatus = statusResponse?.status || activeInvestigation?.status || currentInvestigationItem?.status

  // Load investigation details when activeId changes
  useEffect(() => {
    if (isDemoMode || isSimulationMode) {
      setActiveInvestigation(null)
      setStatusResponse(null)
      setArtifacts([])
      setRankingResult(null)
      setExplainabilityReport(null)
      setApiError(null)
      setIsLoading(false)
      setPlaybackActive(true)
      setPlaybackPlaying(true)
      setPlaybackStage(0)
      setSelectedCandidate(simulationScenario?.candidateVessels[0]?.id || demoCandidateVessels[0].id)
      if (simulationScenario) {
        const created = createSimulationInvestigation('Simulation Investigation', simulationScenario.id, simulationScenario)
        setSimulationTimeline(created.stageSequence)
      }
      return
    }

    if (!activeId) {
      setActiveInvestigation(null)
      setStatusResponse(null)
      setArtifacts([])
      setRankingResult(null)
      setExplainabilityReport(null)
      setApiError(null)
      setIsLoading(false)
      return
    }

    let isMounted = true
    setIsLoading(true)
    setLoadingMessage('Loading investigation telemetry...')
    setApiError(null)

    async function loadData() {
      try {
        const inv = await getInvestigation(activeId)
        if (!isMounted) return
        setActiveInvestigation(inv)

        const [status, artList] = await Promise.all([
          getInvestigationStatus(activeId).catch(() => null),
          getInvestigationArtifacts(activeId).catch(() => []),
        ])

        if (!isMounted) return
        setStatusResponse(status)
        setArtifacts(artList)

        if (status?.completed_stages?.includes('F2') || inv.status === 'COMPLETED') {
          const spillId = resolveSpillId(artList)
          if (spillId) {
            const [ranking, report] = await Promise.all([
              getCandidateRanking(activeId, spillId).catch(() => null),
              getExplainabilityReport(activeId, spillId).catch(() => null),
            ])
            if (isMounted) {
              setRankingResult(ranking)
              setExplainabilityReport(report)
              if (ranking?.candidates && ranking.candidates.length > 0) {
                setSelectedCandidate(ranking.candidates[0].candidate_id || ranking.candidates[0].vessel_id)
              }
            }
          }
        }
      } catch (err) {
        if (!isMounted) return
        setApiError(err instanceof Error ? `Failed to load investigation: ${err.message}` : 'Failed to load investigation')
        setActiveInvestigation(null)
      } finally {
        if (isMounted) setIsLoading(false)
      }
    }

    loadData()

    return () => {
      isMounted = false
    }
  }, [activeId, isDemoMode, isSimulationMode, simulationScenario])

  // Single deferred check when status is PROCESSING
  useEffect(() => {
    if (processingTimerRef.current !== null) {
      window.clearTimeout(processingTimerRef.current)
      processingTimerRef.current = null
    }

    if (!isDemoMode && !isSimulationMode && activeId && currentStatus === 'PROCESSING' && !hasDeferredChecked) {
      processingTimerRef.current = window.setTimeout(async () => {
        setHasDeferredChecked(true)
        try {
          const latestStatus = await getInvestigationStatus(activeId)
          setStatusResponse(latestStatus)
          const latestArtifacts = await getInvestigationArtifacts(activeId).catch(() => [])
          setArtifacts(latestArtifacts)

          if (latestStatus.status === 'COMPLETED') {
            const spillId = resolveSpillId(latestArtifacts)
            if (spillId) {
              const [ranking, report] = await Promise.all([
                getCandidateRanking(activeId, spillId).catch(() => null),
                getExplainabilityReport(activeId, spillId).catch(() => null),
              ])
              setRankingResult(ranking)
              setExplainabilityReport(report)
            }
          }
        } catch {
          // Keep prior state on error
        }
      }, 5000)
    }

    return () => {
      if (processingTimerRef.current !== null) {
        window.clearTimeout(processingTimerRef.current)
        processingTimerRef.current = null
      }
    }
  }, [activeId, isDemoMode, isSimulationMode, currentStatus, hasDeferredChecked])

  // Demo and simulation analysis progression timer
  useEffect(() => {
    if (!playbackPlaying) return undefined

    const stageLimit = isSimulationMode ? simulationTimeline.length - 1 : prototypeFlowStages.length - 1
    const timer = window.setInterval(() => {
      setPlaybackStage((current) => {
        if (current >= stageLimit) {
          setPlaybackPlaying(false)
          return current
        }
        return current + 1
      })
    }, isSimulationMode ? 1400 : 1800)
    return () => window.clearInterval(timer)
  }, [playbackPlaying, isSimulationMode, simulationTimeline.length])

  async function handleRefreshStatus() {
    if (!activeId || isDemoMode || isSimulationMode) return
    setIsLoading(true)
    setLoadingMessage('Refreshing pipeline status...')
    try {
      const latestStatus = await getInvestigationStatus(activeId)
      setStatusResponse(latestStatus)
      const latestArtifacts = await getInvestigationArtifacts(activeId).catch(() => [])
      setArtifacts(latestArtifacts)

      if (latestStatus.status === 'COMPLETED') {
        const spillId = resolveSpillId(latestArtifacts)
        if (spillId) {
          const [ranking, report] = await Promise.all([
            getCandidateRanking(activeId, spillId).catch(() => null),
            getExplainabilityReport(activeId, spillId).catch(() => null),
          ])
          setRankingResult(ranking)
          setExplainabilityReport(report)
        }
      }
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Failed to refresh status')
    } finally {
      setIsLoading(false)
    }
  }

  async function handleRunWorkflow(payload?: InvestigationRunRequest) {
    if (!activeId) return
    setIsRunningWorkflow(true)
    setApiError(null)

    try {
      const result = await runInvestigation(activeId, payload)
      setStatusResponse({
        investigation_id: result.investigation_id,
        status: result.status,
        current_stage: result.current_stage,
        completed_stages: result.completed_stages,
        available_artifacts: Object.values(result.artifacts),
        errors: result.errors,
      })

      const artList = await getInvestigationArtifacts(activeId).catch(() => [])
      setArtifacts(artList)

      const spillId = resolveSpillId(artList)
      if (spillId && result.status === 'COMPLETED') {
        const [ranking, report] = await Promise.all([
          getCandidateRanking(activeId, spillId).catch(() => null),
          getExplainabilityReport(activeId, spillId).catch(() => null),
        ])
        setRankingResult(ranking)
        setExplainabilityReport(report)
      }
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Failed to run workflow')
    } finally {
      setIsRunningWorkflow(false)
    }
  }

  async function handleCreate(req: InvestigationCreateRequest) {
    try {
      const created = await createInvestigation(req)
      onCloseCreateModal()
      if (onInvestigationCreated) {
        await onInvestigationCreated()
      }
      onSelectInvestigation(created.id)
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Failed to create investigation')
    }
  }

  // Extract GIS features
  const safeArtifacts = Array.isArray(artifacts) ? artifacts : []
  const spillAsset = safeArtifacts.find(
    (a) => a.asset_type === 'spill_geometry' || a.source === 'spill_detection'
  )
  const sourceZoneAsset = safeArtifacts.find(
    (a) => a.asset_type === 'drift_product' || a.processing_level === 'derived_source_candidate_zone'
  )

  const aoiBBox = activeInvestigation?.area_of_interest?.bbox
  const liveSpillGeometry = (spillAsset?.metadata?.geometry as GeoJSON.Geometry) || undefined
  const liveSpillCentroid = (spillAsset?.metadata?.centroid as { longitude: number; latitude: number }) || undefined
  const liveSourceZoneGeometry = (sourceZoneAsset?.metadata?.geometry as GeoJSON.Geometry) || undefined

  const visibleLayers = isDemoMode || isSimulationMode
    ? {
        spill: layers.spill && playbackStage >= 1,
        drift: layers.drift && playbackStage >= 2,
        vessels: layers.vessels && playbackStage >= 3,
      }
    : layers

  const visiblePipelineStages = isDemoMode
    ? getPrototypePipelineStages(playbackStage)
    : demoPipelineStages

  // Header Title Text
  const caseTitle = isSimulationMode && simulationScenario
    ? simulationScenario.name
    : isDemoMode
      ? 'Corsica 2018 Demo (Reconstructed Case)'
      : activeInvestigation?.name || 'Active Case File'

  const caseType = isSimulationMode
    ? 'SIMULATION SHOWCASE MODE — FRONTEND-ONLY DEMONSTRATION'
    : isDemoMode
      ? 'Historical Benchmark Reconstruction'
      : activeInvestigation?.status ? `Live G1 Investigation [${activeInvestigation.status}]` : 'Live Investigation'

  const displayInvestigation: InvestigationResponse | null = activeInvestigation || (currentInvestigationItem ? {
    id: currentInvestigationItem.id,
    name: currentInvestigationItem.name,
    status: currentInvestigationItem.status,
    description: currentInvestigationItem.description,
    created_at: currentInvestigationItem.created_at,
    area_of_interest: currentInvestigationItem.area_of_interest || { kind: 'bbox', bbox: { west: -90.5, south: 28.0, east: -89.5, north: 29.0 } },
    time_window: currentInvestigationItem.time_window || { start: '', end: '' },
    metadata: {},
    asset_ids: [],
    evidence_ids: [],
  } : null)

  // Empty state handling
  if (!activeId && !isDemoMode && !isSimulationMode) {
    return (
      <main className="workspace workspace--empty" aria-label="Investigation workspace">
        <div className="empty-workspace-card">
          <div className="empty-workspace-icon">
            <Compass size={28} />
          </div>
          {backendError ? (
            <>
              <h2>Backend Unavailable</h2>
              <p className="empty-workspace-description">
                Unable to connect to the MARIS backend service. Please ensure the API server is active and reachable.
              </p>
              <div className="empty-workspace-actions">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => onInvestigationCreated && onInvestigationCreated()}
                >
                  <RefreshCw size={14} /> Reconnect Server
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={onOpenCreateModal}
                >
                  + New Investigation
                </button>
              </div>
            </>
          ) : (
            <>
              <h2>No Investigation Selected</h2>
              <p className="empty-workspace-description">
                There are currently no active investigations selected. Select an existing case from the header or initialize a new spill investigation.
              </p>
              <div className="empty-workspace-actions">
                <button
                  type="button"
                  className="primary-button"
                  onClick={onOpenCreateModal}
                >
                  + New Investigation
                </button>
              </div>
            </>
          )}
        </div>
        <CreateInvestigationModal
          isOpen={isCreateModalOpen}
          onClose={onCloseCreateModal}
          onSubmit={handleCreate}
        />
      </main>
    )
  }

  return (
    <main className="workspace" aria-label="Investigation workspace">
      {/* Global API Error Banner */}
      {apiError && (
        <div className="workspace-error-banner" role="alert">
          <div className="error-banner-content">
            <AlertTriangle size={18} />
            <div>
              <strong>API Error</strong>
              <p>{apiError}</p>
            </div>
          </div>
          <button
            className="secondary-button"
            type="button"
            onClick={() => onSelectInvestigation(activeId)}
          >
            <RefreshCw size={13} /> Retry
          </button>
        </div>
      )}

      {/* Loading Overlay */}
      {isLoading && (
        <div className="workspace-loading-indicator" style={{
          position: 'absolute',
          top: '1rem',
          left: '50%',
          transform: 'translateX(-50%)',
          zIndex: 30,
          background: 'var(--color-surface)',
          padding: '0.4rem 0.85rem',
          borderRadius: 'var(--radius-xs)',
          border: '1px solid var(--color-border)',
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          fontSize: '0.75rem',
          color: 'var(--color-accent)',
          boxShadow: 'var(--shadow-hud)',
        }}>
          <Loader2 size={14} className="spinner" />
          <span>{loadingMessage}</span>
        </div>
      )}

      {/* Fullscreen Hero Map Canvas */}
      <div className="workspace-hero-layout">
        {/* Compact Unified Top Mission HUD */}
        <div className="workspace-mission-hud">
          <div className="mission-hud-identity">
            <span className="mission-hud-kicker">MISSION CONTROL WORKSPACE</span>
            <span className="mission-hud-case">{caseType}</span>
          </div>
          <div className="mission-hud-status">
            <span className="status-dot status-dot--active" />
            <span>GIS ACTIVE</span>
          </div>
        </div>

        {/* Floating Left Drawer Toggle Button */}
        {!isLeftDrawerOpen && (
          <button
            type="button"
            className="drawer-toggle-tab drawer-toggle-tab--left"
            onClick={() => setIsLeftDrawerOpen(true)}
            title="Open Incident & Satellite Telemetry"
          >
            <PanelLeftOpen size={14} />
            <span>Incident Telemetry</span>
          </button>
        )}

        {/* Floating Right Drawer Toggle Button */}
        {!isRightDrawerOpen && (
          <button
            type="button"
            className="drawer-toggle-tab drawer-toggle-tab--right"
            onClick={() => setIsRightDrawerOpen(true)}
            title="Open Candidate Vessels & Attribution"
          >
            <PanelRightOpen size={14} />
            <span>Attribution</span>
          </button>
        )}

        {/* Floating Left Mission Drawer */}
        <div className={`mission-drawer mission-drawer--left ${!isLeftDrawerOpen ? 'is-collapsed' : ''}`}>
          <div className="drawer-header">
            <h3 className="drawer-title">
              <Activity size={14} /> Incident &amp; Sensor
            </h3>
            <button
              type="button"
              className="icon-button"
              style={{ width: '26px', height: '26px' }}
              onClick={() => setIsLeftDrawerOpen(false)}
              title="Collapse Drawer"
            >
              <PanelLeftClose size={14} />
            </button>
          </div>
          <div className="drawer-body">
            <IncidentPanel
              isDemoMode={isDemoMode}
              isSimulationMode={isSimulationMode}
              simulationScenario={simulationScenario}
              demoIncident={demoIncidentData}
              liveInvestigation={activeInvestigation}
              statusResponse={statusResponse}
              artifacts={artifacts}
              layers={layers}
              onToggleLayer={toggleLayer}
              onRunWorkflow={handleRunWorkflow}
              isRunningWorkflow={isRunningWorkflow}
              error={apiError || backendError}
              onOpenCreateModal={onOpenCreateModal}
              onRefreshStatus={handleRefreshStatus}
              hasDeferredChecked={hasDeferredChecked}
            />
          </div>
        </div>

        {/* Full-bleed Map Canvas Container */}
        <div className="map-canvas-container">
          <MapView
            layers={visibleLayers}
            selectedCandidate={selectedCandidate}
            onSelectCandidate={setSelectedCandidate}
            isDemoMode={isDemoMode}
            isSimulationMode={isSimulationMode}
            simulationScenario={simulationScenario}
            aoiBBox={aoiBBox}
            liveSpillGeometry={liveSpillGeometry}
            liveSpillCentroid={liveSpillCentroid}
            liveSourceZoneGeometry={liveSourceZoneGeometry}
          />
        </div>

        {/* Floating Right Mission Drawer */}
        <div className={`mission-drawer mission-drawer--right ${!isRightDrawerOpen ? 'is-collapsed' : ''}`}>
          <div className="drawer-header">
            <h3 className="drawer-title">
              <Compass size={14} /> Candidates &amp; Attribution
            </h3>
            <button
              type="button"
              className="icon-button"
              style={{ width: '26px', height: '26px' }}
              onClick={() => setIsRightDrawerOpen(false)}
              title="Collapse Drawer"
            >
              <PanelRightClose size={14} />
            </button>
          </div>
          <div className="drawer-body">
            <AnalysisPanel
              isDemoMode={isDemoMode}
              isSimulationMode={isSimulationMode}
              simulationScenario={simulationScenario}
              simulationStageIndex={playbackStage}
              simulationStages={simulationTimeline}
              onSkipSimulation={() => {
                setPlaybackStage(simulationTimeline.length - 1)
                setPlaybackPlaying(false)
              }}
              onReplaySimulation={() => {
                setPlaybackStage(0)
                setPlaybackPlaying(true)
              }}
              onSelectSimulationStage={(idx) => {
                setPlaybackStage(idx)
                if (idx >= simulationTimeline.length - 1) {
                  setPlaybackPlaying(false)
                }
              }}
              demoIncident={demoIncidentData}
              demoCandidates={demoCandidateVessels}
              selectedCandidate={selectedCandidate}
              onSelectCandidate={setSelectedCandidate}
              prototypeActive={playbackActive}
              rankingResult={rankingResult}
              explainabilityReport={explainabilityReport}
            />
          </div>
        </div>

        {/* Floating Historical Playback Controller */}
        {isDemoMode && (
          <div className="workspace-playback-floating">
            <InvestigationPlayback
              active={playbackActive}
              playing={playbackPlaying}
              stageIndex={playbackStage}
              stages={prototypeFlowStages}
              onStart={() => {
                setPlaybackActive(true)
                setPlaybackPlaying(true)
              }}
              onPause={() => setPlaybackPlaying(false)}
              onReset={() => {
                setPlaybackActive(true)
                setPlaybackStage(0)
                setPlaybackPlaying(true)
              }}
              onClose={() => {
                setPlaybackActive(false)
                setPlaybackPlaying(false)
                setPlaybackStage(0)
              }}
            />
          </div>
        )}

        {/* Floating Bottom Pipeline Stepper HUD */}
        <div className="workspace-bottom-hud">
          <InvestigationPipeline
            isDemoMode={isDemoMode}
            isSimulationMode={isSimulationMode}
            simulationStages={simulationTimeline}
            simulationStageIndex={playbackStage}
            onSelectStage={(idx) => {
              setPlaybackStage(idx)
              if (idx >= simulationTimeline.length - 1) {
                setPlaybackPlaying(false)
              }
            }}
            demoStages={visiblePipelineStages}
            completedStages={statusResponse?.completed_stages}
            currentStage={statusResponse?.current_stage}
            workflowStatus={statusResponse?.status || activeInvestigation?.status}
            isExecuting={isRunningWorkflow || statusResponse?.status === 'PROCESSING'}
          />
        </div>
      </div>

      <CreateInvestigationModal
        isOpen={isCreateModalOpen}
        onClose={onCloseCreateModal}
        onSubmit={handleCreate}
        onNavigateToEvaluator={onNavigateToEvaluator}
      />
    </main>
  )
}