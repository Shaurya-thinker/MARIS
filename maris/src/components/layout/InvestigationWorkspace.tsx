import { useEffect, useRef, useState } from 'react'
import { candidateVessels, incidentData, pipelineStages } from '../../data/demoData'
import { AnalysisPanel } from '../analysis/AnalysisPanel'
import { IncidentPanel } from '../incident/IncidentPanel'
import { MapView } from '../map/MapView'
import { InvestigationPipeline } from '../pipeline/InvestigationPipeline'
import { InvestigationPlayback } from '../../prototype/InvestigationPlayback'
import { getPrototypePipelineStages, prototypeFlowStages } from '../../prototype/prototypeFlow'

export function InvestigationWorkspace() {
  const [layers, setLayers] = useState({ spill: true, drift: true, vessels: true })
  const [selectedCandidate, setSelectedCandidate] = useState(candidateVessels[0].id)
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [playbackActive, setPlaybackActive] = useState(false)
  const [playbackPlaying, setPlaybackPlaying] = useState(false)
  const [playbackStage, setPlaybackStage] = useState(0)
  const analysisTimerRef = useRef<number | null>(null)
  function toggleLayer(layer: 'spill' | 'drift' | 'vessels') { setLayers((current) => ({ ...current, [layer]: !current[layer] })) }

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

  useEffect(() => () => {
    if (analysisTimerRef.current !== null) window.clearTimeout(analysisTimerRef.current)
  }, [])

  const visibleLayers = playbackActive ? {
    spill: layers.spill && playbackStage >= 1,
    drift: layers.drift && playbackStage >= 2,
    vessels: layers.vessels && playbackStage >= 4,
  } : layers
  const visiblePipeline = playbackActive ? getPrototypePipelineStages(playbackStage) : pipelineStages

  function analyzeScene() {
    if (analysisTimerRef.current !== null) window.clearTimeout(analysisTimerRef.current)
    setIsAnalyzing(true)
    analysisTimerRef.current = window.setTimeout(() => {
      setIsAnalyzing(false)
      analysisTimerRef.current = null
    }, 1200)
  }

  function startPlayback() { setPlaybackActive(true); setPlaybackPlaying(true) }
  function resetPlayback() { setPlaybackActive(true); setPlaybackStage(0); setPlaybackPlaying(true) }
  function closePlayback() { setPlaybackActive(false); setPlaybackPlaying(false); setPlaybackStage(0) }

  return <main className="workspace"><InvestigationPlayback active={playbackActive} playing={playbackPlaying} stageIndex={playbackStage} stages={prototypeFlowStages} onStart={startPlayback} onPause={() => setPlaybackPlaying(false)} onReset={resetPlayback} onClose={closePlayback} /><div className="workspace-grid"><IncidentPanel incident={incidentData} layers={layers} onToggleLayer={toggleLayer} onAnalyze={analyzeScene} isAnalyzing={isAnalyzing} /><MapView layers={visibleLayers} selectedCandidate={selectedCandidate} onSelectCandidate={setSelectedCandidate} /><AnalysisPanel incident={incidentData} candidates={candidateVessels} selectedCandidate={selectedCandidate} onSelectCandidate={setSelectedCandidate} prototypeActive={playbackActive} /></div><InvestigationPipeline stages={visiblePipeline} /></main>
}