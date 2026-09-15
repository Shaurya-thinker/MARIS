import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { candidateVessels as demoCandidateVessels, incidentData as demoIncidentData, pipelineStages as demoPipelineStages } from '../../data/demoData'
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
  listInvestigations,
  runInvestigation,
} from '../../api/investigationApi'
import type {
  ArtifactSummary,
  CandidateRanking,
  ExplainabilityReport,
  InvestigationCreateRequest,
  InvestigationListItem,
  InvestigationResponse,
  InvestigationStatusResponse,
} from '../../types/investigationApi'

export function InvestigationWorkspace({
  activeId,
  isDemoMode,
  investigations,
  onSelectInvestigation,
  isCreateModalOpen,
  onCloseCreateModal,
  onOpenCreateModal,
  backendError,
}: {
  activeId: string
  isDemoMode: boolean
  investigations: InvestigationListItem[]
  onSelectInvestigation: (id: string) => void
  isCreateModalOpen: boolean
  onCloseCreateModal: () => void
  onOpenCreateModal: () => void
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

  function toggleLayer(layer: 'spill' | 'drift' | 'vessels') {
    setLayers((current) => ({ ...current, [layer]: !current[layer] }))
  }

  // Load investigation details when activeId changes
  useEffect(() => {
    if (isDemoMode) {
      setActiveInvestigation(null)
      setStatusResponse(null)
      setArtifacts([])
      setRankingResult(null)
      setExplainabilityReport(null)
      setSelectedCandidate(demoCandidateVessels[0].id)
      setApiError(null)
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

        // Find spill asset or detection ID to load ranking & explainability if available
        const spillAsset = artList.find(
          (a) => a.asset_type === 'spill_geometry' || a.source === 'spill_detection'
        )
        const rankingAsset = artList.find(
          (a) => a.metadata?.asset_type === 'candidate_ranking' || a.provenance?.extra?.stage === 'F2'
        )

        const spillId =
          (spillAsset?.metadata?.detection_id as string | undefined) ||
          spillAsset?.provenance?.product_id ||
          spillAsset?.id

        if (spillId) {
          try {
            const ranking = await getCandidateRanking(
              activeId,
              spillId,
              rankingAsset?.id
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
                rankingAsset?.id
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

  // Workflow run execution
  async function handleRunWorkflow() {
    if (isDemoMode || !activeId) return

    setIsRunningWorkflow(true)
    setLoadingMessage('Running investigation pipeline (B1 → F3)...')
    setApiError(null)

    try {
      const runRes = await runInvestigation(activeId)
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

      // Resolve spill_id
      const spillId = runRes.artifacts.B3 || artList.find((a) => a.asset_type === 'spill_geometry')?.id
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

  // Handle new investigation creation
  async function handleCreate(payload: InvestigationCreateRequest) {
    const created = await createInvestigation(payload)
    onSelectInvestigation(created.id)
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

  const visibleLayers = isDemoMode && playbackActive
    ? {
        spill: layers.spill && playbackStage >= 1,
        drift: layers.drift && playbackStage >= 2,
        vessels: layers.vessels && playbackStage >= 4,
      }
    : layers

  const visiblePipelineStages = isDemoMode && playbackActive
    ? getPrototypePipelineStages(playbackStage)
    : demoPipelineStages

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
      />

      <CreateInvestigationModal
        isOpen={isCreateModalOpen}
        onClose={onCloseCreateModal}
        onSubmit={handleCreate}
      />
    </main>
  )
}