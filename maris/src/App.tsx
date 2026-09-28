import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { Header, type MarisView } from './components/layout/Header'
import { AlertsDrawer } from './components/layout/AlertsDrawer'
import { InvestigationWorkspace, resolveSpillId } from './components/layout/InvestigationWorkspace'
import { MainLayout } from './components/layout/MainLayout'
import { WorkspaceErrorBoundary } from './components/layout/WorkspaceErrorBoundary'
import { InvestigationsView } from './components/views/InvestigationsView'
import { EvidenceView } from './components/views/EvidenceView'
import { VesselsView } from './components/views/VesselsView'
import { AnalyticsView } from './components/views/AnalyticsView'
import { PipelineView } from './components/views/PipelineView'
import {
  getCandidateRanking,
  getExplainabilityReport,
  getInvestigationArtifacts,
  listInvestigations,
} from './api/investigationApi'
import { candidateVessels as demoCandidateVessels, incidentData as demoIncidentData } from './data/demoData'
import { getSimulationScenarioById, simulationScenarios } from './simulation/simulationEngine'
import type {
  ArtifactSummary,
  CandidateRanking,
  ExplainabilityReport,
  InvestigationListItem,
} from './types/investigationApi'
import RealExperimentView from './components/views/RealExperimentView'
import { usePageTransition } from './lib/motion'

function getInitialViewFromLocation(): MarisView {
  if (typeof window === 'undefined') return 'workspace'
  const path = window.location.pathname.replace(/^\/+/, '').toLowerCase()
  if (path === 'evaluator' || path === 'experiment') return 'evaluator'
  if (path === 'investigations') return 'investigations'
  if (path === 'overview') return 'overview'
  if (path === 'evidence') return 'evidence'
  if (path === 'vessels') return 'vessels'
  if (path === 'analytics') return 'analytics'
  if (path === 'pipeline') return 'pipeline'
  const hash = window.location.hash.replace(/^#\/?/, '').toLowerCase()
  if (hash === 'evaluator' || hash === 'experiment') return 'evaluator'
  if (hash === 'investigations') return 'investigations'
  if (hash === 'overview') return 'overview'
  if (hash === 'evidence') return 'evidence'
  if (hash === 'vessels') return 'vessels'
  if (hash === 'analytics') return 'analytics'
  if (hash === 'pipeline') return 'pipeline'
  return 'workspace'
}

export function App() {
  const [investigations, setInvestigations] = useState<InvestigationListItem[]>([])
  const [activeInvestigationId, setActiveInvestigationId] = useState<string>('')
  const [activeView, setActiveView] = useState<MarisView>(getInitialViewFromLocation)
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isAlertsDrawerOpen, setIsAlertsDrawerOpen] = useState(false)
  const [initError, setInitError] = useState<string | null>(null)
  const [isLoadingList, setIsLoadingList] = useState(false)
  const [selectedCandidate, setSelectedCandidate] = useState<string>(demoCandidateVessels[0].id)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [selectedEvaluatorInvId, setSelectedEvaluatorInvId] = useState<string | null>(null)
  const [selectedExperimentMode, setSelectedExperimentMode] = useState<'real' | 'evaluator' | 'synthetic'>('evaluator')
  const [activeArtifacts, setActiveArtifacts] = useState<ArtifactSummary[]>([])
  const [activeRankingResult, setActiveRankingResult] = useState<CandidateRanking | null>(null)
  const [activeExplainabilityReport, setActiveExplainabilityReport] = useState<ExplainabilityReport | null>(null)
  const pageContainerRef = usePageTransition<HTMLDivElement>(activeView)

  const handleSelectView = useCallback((view: MarisView) => {
    setActiveView(view)
    if (view === 'evaluator') {
      setSelectedExperimentMode('evaluator')
      setSelectedRunId(null)
      setSelectedEvaluatorInvId(null)
    }
    if (typeof window !== 'undefined') {
      const targetPath = view === 'workspace' ? '/' : `/${view}`
      if (window.location.pathname !== targetPath) {
        window.history.pushState(null, '', targetPath)
      }
    }
  }, [])

  const handleNavigateToEvaluator = useCallback((opts?: {
    mode?: 'real' | 'evaluator';
    runId?: string;
    investigationId?: string;
  }) => {
    if (opts?.runId) {
      setSelectedRunId(opts.runId)
      setSelectedEvaluatorInvId(null)
      setSelectedExperimentMode('real')
    } else if (opts?.investigationId) {
      setSelectedEvaluatorInvId(opts.investigationId)
      setSelectedRunId(null)
      setSelectedExperimentMode('evaluator')
    } else {
      setSelectedRunId(null)
      setSelectedEvaluatorInvId(null)
      setSelectedExperimentMode(opts?.mode ?? 'evaluator')
    }
    setActiveView('evaluator')
    if (typeof window !== 'undefined') {
      const targetPath = '/evaluator'
      if (window.location.pathname !== targetPath) {
        window.history.pushState(null, '', targetPath)
      }
    }
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return undefined
    const onLocationChange = () => {
      const view = getInitialViewFromLocation()
      setActiveView(view)
      if (view === 'evaluator') {
        setSelectedExperimentMode('evaluator')
      }
    }
    window.addEventListener('popstate', onLocationChange)
    window.addEventListener('hashchange', onLocationChange)
    return () => {
      window.removeEventListener('popstate', onLocationChange)
      window.removeEventListener('hashchange', onLocationChange)
    }
  }, [])

  const loadInvestigationsList = useCallback(async () => {
    setIsLoadingList(true)
    setInitError(null)
    try {
      const list = await listInvestigations()
      setInvestigations(list)
      if (list.length > 0) {
        setActiveInvestigationId((curr) => {
          if (curr === 'corsica-2018-demo' || curr.startsWith('simulation-')) return curr
          const exists = list.some((item) => item.id === curr)
          return exists ? curr : list[0].id
        })
      } else {
        setActiveInvestigationId((curr) => (curr === 'corsica-2018-demo' || curr.startsWith('simulation-') ? curr : ''))
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setInitError(`Unable to connect to MARIS backend: ${msg}`)
      setActiveInvestigationId((curr) => (curr === 'corsica-2018-demo' || curr.startsWith('simulation-') ? curr : ''))
    } finally {
      setIsLoadingList(false)
    }
  }, [])

  useEffect(() => {
    loadInvestigationsList()
  }, [loadInvestigationsList])

  useEffect(() => {
    let isCancelled = false
    if (!activeInvestigationId || activeInvestigationId === 'corsica-2018-demo' || activeInvestigationId.startsWith('simulation-')) {
      setActiveArtifacts([])
      setActiveRankingResult(null)
      setActiveExplainabilityReport(null)
      return
    }

    async function fetchInvestigationTelemetry() {
      try {
        const artifacts = await getInvestigationArtifacts(activeInvestigationId).catch(() => [])
        if (isCancelled) return
        setActiveArtifacts(artifacts)

        const spillId = resolveSpillId(artifacts)
        if (spillId) {
          const [ranking, report] = await Promise.all([
            getCandidateRanking(activeInvestigationId, spillId).catch(() => null),
            getExplainabilityReport(activeInvestigationId, spillId).catch(() => null),
          ])
          if (!isCancelled) {
            setActiveRankingResult(ranking)
            setActiveExplainabilityReport(report)
            if (ranking?.candidates && ranking.candidates.length > 0) {
              const firstId = ranking.candidates[0].candidate_id || ranking.candidates[0].vessel_id
              if (firstId) setSelectedCandidate(firstId)
            }
          }
        } else {
          if (!isCancelled) {
            setActiveRankingResult(null)
            setActiveExplainabilityReport(null)
          }
        }
      } catch {
        if (!isCancelled) {
          setActiveArtifacts([])
          setActiveRankingResult(null)
          setActiveExplainabilityReport(null)
        }
      }
    }

    fetchInvestigationTelemetry()

    return () => {
      isCancelled = true
    }
  }, [activeInvestigationId])

  const isDemoMode = activeInvestigationId === 'corsica-2018-demo'
  const isSimulationMode = activeInvestigationId.startsWith('simulation-')
  const activeListItem = investigations.find((i) => i.id === activeInvestigationId)
  const activeSimulationScenario = isSimulationMode
    ? getSimulationScenarioById(activeInvestigationId.replace('simulation-', '')) ?? null
    : null

  return (
    <MainLayout>
      <Header
        activeId={activeInvestigationId}
        investigations={investigations}
        activeStatus={activeListItem?.status}
        isDemoMode={isDemoMode}
        isSimulationMode={isSimulationMode}
        isBackendUnavailable={Boolean(initError)}
        simulationScenarios={simulationScenarios}
        onSelectInvestigation={(id) => {
          setActiveInvestigationId(id)
        }}
        onOpenCreateModal={() => setIsCreateModalOpen(true)}
        activeView={activeView}
        onSelectView={handleSelectView}
        onOpenAlertsDrawer={() => setIsAlertsDrawerOpen(true)}
      />

      <AlertsDrawer
        isOpen={isAlertsDrawerOpen}
        onClose={() => setIsAlertsDrawerOpen(false)}
      />

      {initError && (
        <div className="workspace-error-banner" role="alert">
          <div className="error-banner-content">
            <AlertTriangle size={16} />
            <div>
              <strong>Backend Unavailable</strong>
              <p>{initError}</p>
            </div>
          </div>
          <button
            className="secondary-button"
            type="button"
            onClick={loadInvestigationsList}
            disabled={isLoadingList}
          >
            <RefreshCw size={13} className={isLoadingList ? 'spinner' : ''} /> Retry Connection
          </button>
        </div>
      )}

      {/* View Switcher Routing */}
      <div key={activeView} ref={pageContainerRef} className="page-transition-wrapper">
        {activeView === 'workspace' && (
          <WorkspaceErrorBoundary onReset={loadInvestigationsList}>
            <InvestigationWorkspace
              activeId={activeInvestigationId}
              isDemoMode={isDemoMode}
              isSimulationMode={isSimulationMode}
              simulationScenario={activeSimulationScenario}
              investigations={investigations}
              onSelectInvestigation={setActiveInvestigationId}
              isCreateModalOpen={isCreateModalOpen}
              onCloseCreateModal={() => setIsCreateModalOpen(false)}
              onOpenCreateModal={() => setIsCreateModalOpen(true)}
              onInvestigationCreated={loadInvestigationsList}
              backendError={initError}
              onNavigateToEvaluator={handleNavigateToEvaluator}
            />
          </WorkspaceErrorBoundary>
        )}

        {activeView === 'evaluator' && (
          <WorkspaceErrorBoundary onReset={loadInvestigationsList}>
            <RealExperimentView
              initialMode={selectedExperimentMode}
              initialRunId={selectedRunId}
              initialInvestigationId={selectedEvaluatorInvId}
            />
          </WorkspaceErrorBoundary>
        )}

        {activeView === 'overview' && (
          <InvestigationsView
            investigations={investigations}
            activeId={activeInvestigationId}
            onSelectInvestigation={setActiveInvestigationId}
            onNavigateToView={(view, opts) => {
              if (opts?.runId) {
                setSelectedRunId(opts.runId)
                setSelectedEvaluatorInvId(null)
              } else if (opts?.investigationId) {
                setSelectedEvaluatorInvId(opts.investigationId)
                setSelectedRunId(null)
              }
              handleSelectView(view)
            }}
            onOpenCreateModal={() => setIsCreateModalOpen(true)}
            simulationScenarios={simulationScenarios}
            isBackendUnavailable={Boolean(initError)}
          />
        )}

        {activeView === 'investigations' && (
          <InvestigationsView
            investigations={investigations}
            activeId={activeInvestigationId}
            onSelectInvestigation={setActiveInvestigationId}
            onNavigateToView={(view, opts) => {
              if (opts?.runId) {
                setSelectedRunId(opts.runId)
                setSelectedEvaluatorInvId(null)
              } else if (opts?.investigationId) {
                setSelectedEvaluatorInvId(opts.investigationId)
                setSelectedRunId(null)
              }
              handleSelectView(view)
            }}
            onOpenCreateModal={() => setIsCreateModalOpen(true)}
            simulationScenarios={simulationScenarios}
            isBackendUnavailable={Boolean(initError)}
          />
        )}

        {activeView === 'evidence' && (
          <EvidenceView
            isDemoMode={isDemoMode}
            isSimulationMode={isSimulationMode}
            simulationScenario={activeSimulationScenario}
            demoIncident={demoIncidentData}
            artifacts={activeArtifacts}
            activeId={activeInvestigationId}
            onNavigateToView={handleSelectView}
          />
        )}

        {activeView === 'vessels' && (
          <VesselsView
            isDemoMode={isDemoMode}
            isSimulationMode={isSimulationMode}
            simulationScenario={activeSimulationScenario}
            demoCandidates={demoCandidateVessels}
            rankingResult={activeRankingResult}
            selectedCandidate={selectedCandidate}
            onSelectCandidate={setSelectedCandidate}
            onNavigateToView={handleSelectView}
          />
        )}

        {activeView === 'analytics' && (
          <AnalyticsView
            isDemoMode={isDemoMode}
            isSimulationMode={isSimulationMode}
            simulationScenario={activeSimulationScenario}
            rankingResult={activeRankingResult}
            explainabilityReport={activeExplainabilityReport}
            onNavigateToView={handleSelectView}
          />
        )}

        {activeView === 'pipeline' && (
          <PipelineView
            onNavigateToView={handleSelectView}
          />
        )}
      </div>
    </MainLayout>
  )
}

export default App