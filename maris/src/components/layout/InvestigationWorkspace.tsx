import { useEffect, useRef, useState } from 'react'
import { Activity, AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
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
 *
 * Priority order:
 * 1. metadata.detection_id — Detection UUID assigned during Stage B3 spill detection.
 * 2. provenance.product_id — Product/asset provenance identifier when available.
 * 3. asset.id / asset.asset_id — Unique registered asset identifier in the AssetRegistry.
 *
 * No arithmetic or scientific computation is performed.
 */
export function resolveSpillId(artifacts: ArtifactSummary[]): string | null {
  const spillAsset = artifacts.find(
    (a) => a.asset_type === 'spill_geometry' || a.source === 'spill_detection'
  )
  if (!spillAsset) return null

  // Priority 1: metadata.detection_id
  const detectionId = spillAsset.metadata?.detection_id
  if (typeof detectionId === 'string' && detectionId.trim().length > 0) {
    return detectionId.trim()
  }

  // Priority 2: provenance.product_id
  const productId = spillAsset.provenance?.product_id
  if (typeof productId === 'string' && productId.trim().length > 0) {
    return productId.trim()
  }

  // Priority 3: asset.id / asset.asset_id
  const directId = spillAsset.asset_id || (spillAsset as unknown as { id?: string }).id
  if (typeof directId === 'string' && directId.trim().length > 0) {
    return directId.trim()
  }

  return null
}

export function InvestigationWorkspace({
  activeId,
  isDemoMode,
  isSimulationMode,
  simulationScenario,
  investigations,
  onSelectInvestigation,
  isCreateModalOpen,
  onCloseCreateModal,
  onOpenCreateModal,
  onInvestigationCreated,
  backendError,
}: {
  activeId: string
  isDemoMode: boolean
  isSimulationMode: boolean
  simulationScenario: SimulationScenario | null
  investigations: InvestigationListItem[]
  onSelectInvestigation: (id: string) => void
  isCreateModalOpen: boolean
  onCloseCreateModal: () => void
  onOpenCreateModal: () => void
  onInvestigationCreated?: () => Promise<void> | void
  backendError?: string | null
}) {
  const [layers, setLayers] = useState({ spill: true, drift: true, vessels: true })
  const [selectedCandidate, setSelectedCandidate] = useState(demoCandidateVessels[0].id)

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

  // Load investigation details when activeId changes
  useEffect(() => {
    if (isDemoMode || isSimulationMode) {
      setActiveInvestigation(null)
      setStatusResponse(null)
      setArtifacts([])
      setRankingResult(null)
      setExplainabilityReport(null)
      setSelectedCandidate(demoCandidateVessels[0].id)
      setApiError(null)
      if (simulationScenario) {
        setSimulationTimeline(createSimulationInvestigation('Simulation Investigation', simulationScenario.id, simulationScenario).stageSequence)
      }
      return
    }

    if (!activeId) return

    let isMounted = true
    async function fetchInvestigationDetails() {
      setIsLoading(true)
      setLoadingMessage('Loading investigation details...')
      setApiError(null)

      try {
        const [inv, st, artList] = await Promise.all([
          getInvestigation(activeId),
          getInvestigationStatus(activeId),
          getInvestigationArtifacts(activeId),
        ])

        if (!isMounted) return

        setActiveInvestigation(inv)
        setStatusResponse(st)
        setArtifacts(artList)

        // Stage G3: PROCESSING recovery — perform exactly one deferred check after 5s
        if (st.status === 'PROCESSING') {
          setHasDeferredChecked(false)
          if (processingTimerRef.current) {
            window.clearTimeout(processingTimerRef.current)
          }
          processingTimerRef.current = window.setTimeout(async () => {
            try {
              const latestStatus = await getInvestigationStatus(activeId)
              if (!isMounted) return
              setStatusResponse(latestStatus)
              setHasDeferredChecked(true)
              if (latestStatus.status === 'COMPLETED' || latestStatus.status === 'FAILED') {
                const [updatedInv, latestArtList] = await Promise.all([
                  getInvestigation(activeId),
                  getInvestigationArtifacts(activeId),
                ])
                if (!isMounted) return
                setActiveInvestigation(updatedInv)
                setArtifacts(latestArtList)
                const resolvedSpill = resolveSpillId(latestArtList)
                if (resolvedSpill && latestStatus.completed_stages.includes('F2')) {
                  try {
                    const ranking = await getCandidateRanking(activeId, resolvedSpill)
                    setRankingResult(ranking)
                    if (latestStatus.completed_stages.includes('F3')) {
                      const report = await getExplainabilityReport(activeId, resolvedSpill)
                      setExplainabilityReport(report)
                    }
                  } catch {
                    // downstream fetch non-fatal
                  }
                }
              }
            } catch (pollErr) {
              console.warn('Deferred status check failed:', pollErr)
              if (isMounted) setHasDeferredChecked(true)
            }
          }, 5000)
        }

        // Find spill asset using resolveSpillId()
        const spillId = resolveSpillId(artList)
        const rankingAsset = artList.find(
          (a) => a.metadata?.asset_type === 'candidate_ranking' || a.provenance?.extra?.stage === 'F2'
        )

        if (spillId) {
          try {
            const ranking = await getCandidateRanking(
              activeId,
              spillId,
              rankingAsset?.asset_id || (rankingAsset as unknown as { id?: string })?.id
            )
            if (isMounted) {
              setRankingResult(ranking)
              if (ranking.candidates.length > 0) {
                setSelectedCandidate(ranking.candidates[0].candidate_id || ranking.candidates[0].vessel_id)
              }
            }

            try {
              const report = await getExplainabilityReport(
                activeId,
                spillId,
                rankingAsset?.asset_id || (rankingAsset as unknown as { id?: string })?.id
              )
              if (isMounted) setExplainabilityReport(report)
            } catch {
              // F3 report may not be generated yet
            }
          } catch {
            // Ranking may not be generated yet
          }
        }
      } catch (err) {
        if (!isMounted) return
        const msg = err instanceof Error ? err.message : String(err)
        setApiError(`Failed to load investigation: ${msg}`)
      } finally {
        if (isMounted) setIsLoading(false)
      }
    }

    fetchInvestigationDetails()

    return () => {
      isMounted = false
      if (processingTimerRef.current) {
        window.clearTimeout(processingTimerRef.current)
      }
    }
  }, [activeId, isDemoMode])

  // Demo playback timer
  useEffect(() => {
    if (!playbackPlaying) return undefined
    const timer = window.setInterval(() => {
      setPlaybackStage((current) => {
        if (current >= prototypeFlowStages.length - 1) {
          setPlaybackPlaying(false)
          return current
        }
        return current + 1
      })
    }, 1800)
    return () => window.clearInterval(timer)
  }, [playbackPlaying])

  // Manual status refresh for PROCESSING recovery
  async function handleRefreshStatus() {
    if (!activeId || isDemoMode) return
    setIsLoading(true)
    setLoadingMessage('Refreshing status...')
    try {
      const st = await getInvestigationStatus(activeId)
      setStatusResponse(st)
      if (st.status === 'COMPLETED' || st.status === 'FAILED') {
        const [updatedInv, artList] = await Promise.all([
          getInvestigation(activeId),
          getInvestigationArtifacts(activeId),
        ])
        setActiveInvestigation(updatedInv)
        setArtifacts(artList)
        const spillId = resolveSpillId(artList)
        if (spillId && st.completed_stages.includes('F2')) {
          try {
            const ranking = await getCandidateRanking(activeId, spillId)
            setRankingResult(ranking)
            if (st.completed_stages.includes('F3')) {
              const report = await getExplainabilityReport(activeId, spillId)
              setExplainabilityReport(report)
            }
          } catch {
            // non-fatal
          }
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setApiError(`Failed to refresh status: ${msg}`)
    } finally {
      setIsLoading(false)
    }
  }

  // Workflow run execution with optional run configuration payload
  async function handleRunWorkflow(payload?: InvestigationRunRequest) {
    if (isDemoMode || !activeId) return

    setIsRunningWorkflow(true)
    setLoadingMessage('Running investigation pipeline (B1 → F3)...')
    setApiError(null)

    try {
      const runRes = await runInvestigation(activeId, payload)
      setStatusResponse({
        investigation_id: runRes.investigation_id,
        status: runRes.status,
        current_stage: runRes.current_stage,
        completed_stages: runRes.completed_stages,
        available_artifacts: Object.values(runRes.artifacts),
        errors: runRes.errors,
      })

      // Refresh investigation and artifacts
      const [updatedInv, artList] = await Promise.all([
        getInvestigation(activeId),
        getInvestigationArtifacts(activeId),
      ])
      setActiveInvestigation(updatedInv)
      setArtifacts(artList)

      // Resolve spill_id using resolveSpillId()
      const spillId = resolveSpillId(artList) || runRes.artifacts.B3
      if (spillId && runRes.completed_stages.includes('F2')) {
        try {
          const ranking = await getCandidateRanking(activeId, spillId, runRes.artifacts.F2)
          setRankingResult(ranking)
          if (ranking.candidates.length > 0) {
            setSelectedCandidate(ranking.candidates[0].candidate_id || ranking.candidates[0].vessel_id)
          }

          if (runRes.completed_stages.includes('F3')) {
            const report = await getExplainabilityReport(activeId, spillId, runRes.artifacts.F2)
            setExplainabilityReport(report)
          }
        } catch (fetchErr) {
          console.warn('Failed to retrieve downstream ranking/report:', fetchErr)
        }
      }

      if (runRes.status === 'FAILED' && runRes.errors.length > 0) {
        setApiError(`Pipeline halted at Stage ${runRes.errors[0].stage}: ${runRes.errors[0].message}`)
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setApiError(`Workflow run failed: ${msg}`)
    } finally {
      setIsRunningWorkflow(false)
    }
  }

  // Handle new investigation creation and list refresh
  async function handleCreate(payload: InvestigationCreateRequest | { mode: 'SIMULATION'; scenarioId: string; name: string; region: string; timestamp: string; scenario: SimulationScenario }) {
    if ('mode' in payload && payload.mode === 'SIMULATION') {
      const created = createSimulationInvestigation(payload.name, payload.scenarioId, payload.scenario)
      setSimulationTimeline(created.stageSequence)
      setPlaybackActive(true)
      setPlaybackPlaying(true)
      setPlaybackStage(0)
      if (onInvestigationCreated) {
        await onInvestigationCreated()
      }
      onSelectInvestigation(created.id)
      onCloseCreateModal()
      return
    }

    const created = await createInvestigation(payload)
    if (onInvestigationCreated) {
      await onInvestigationCreated()
    }
    onSelectInvestigation(created.id)
    onCloseCreateModal()
  }

  // Geographic assets extraction for live MapLibre rendering
  const aoiBBox =
    activeInvestigation?.area_of_interest?.kind === 'bbox'
      ? activeInvestigation.area_of_interest.bbox
      : null

  const spillArtifact = artifacts.find(
    (a) => a.asset_type === 'spill_geometry' || a.source === 'spill_detection'
  )
  const liveSpillGeometry = (spillArtifact?.metadata?.geometry as Record<string, unknown> | undefined) || null
  const liveSpillCentroid = (spillArtifact?.metadata?.centroid as { longitude: number; latitude: number } | undefined) || null

  const sourceZoneArtifact = artifacts.find(
    (a) => a.asset_type === 'drift_product' && (a.source === 'source_estimation_service' || a.processing_level === 'derived_source_candidate_zone')
  )
  const liveSourceZoneGeometry = (sourceZoneArtifact?.metadata?.geometry as Record<string, unknown> | undefined) || null

  const visibleLayers = (isDemoMode || isSimulationMode) && playbackActive
    ? {
        spill: layers.spill && playbackStage >= 1,
        drift: layers.drift && playbackStage >= 2,
        vessels: layers.vessels && playbackStage >= 4,
      }
    : layers

  const visiblePipelineStages = (isDemoMode || isSimulationMode) && playbackActive
    ? getPrototypePipelineStages(playbackStage)
    : demoPipelineStages

  if (!activeId && !isDemoMode) {
    return (
      <main className="workspace workspace--empty" aria-label="Investigation workspace">
        <div className="empty-workspace-card">
          {backendError ? (
            <>
              <AlertTriangle size={36} className="empty-workspace-icon error-icon" />
              <h2>Backend Unavailable</h2>
              <p className="empty-workspace-description">
                Unable to connect to the MARIS backend service. Ensure the backend server is running and accessible.
              </p>
              <div className="empty-workspace-actions">
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
              <Activity size={36} className="empty-workspace-icon" />
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
    <main className="workspace">
      {isDemoMode && (
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
      )}

      {/* Global API Error Banner (No mock fallback!) */}
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
        <div className="workspace-loading-indicator">
          <Loader2 size={16} className="spinner" />
          <span>{loadingMessage}</span>
        </div>
      )}

      <div className="workspace-grid">
        <IncidentPanel
          isDemoMode={isDemoMode}
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

        <MapView
          layers={visibleLayers}
          selectedCandidate={selectedCandidate}
          onSelectCandidate={setSelectedCandidate}
          isDemoMode={isDemoMode}
          aoiBBox={aoiBBox}
          liveSpillGeometry={liveSpillGeometry}
          liveSpillCentroid={liveSpillCentroid}
          liveSourceZoneGeometry={liveSourceZoneGeometry}
        />

        <AnalysisPanel
          isDemoMode={isDemoMode}
          demoIncident={demoIncidentData}
          demoCandidates={demoCandidateVessels}
          selectedCandidate={selectedCandidate}
          onSelectCandidate={setSelectedCandidate}
          prototypeActive={playbackActive}
          rankingResult={rankingResult}
          explainabilityReport={explainabilityReport}
        />
      </div>

      <InvestigationPipeline
        isDemoMode={isDemoMode}
        demoStages={visiblePipelineStages}
        completedStages={statusResponse?.completed_stages}
        currentStage={statusResponse?.current_stage}
        workflowStatus={statusResponse?.status || activeInvestigation?.status}
        isExecuting={isRunningWorkflow || statusResponse?.status === 'PROCESSING'}
      />

      <CreateInvestigationModal
        isOpen={isCreateModalOpen}
        onClose={onCloseCreateModal}
        onSubmit={handleCreate}
      />
    </main>
  )
}