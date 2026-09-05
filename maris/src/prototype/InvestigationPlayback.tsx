import { Pause, Play, RotateCcw, X } from 'lucide-react'
import type { PrototypeFlowStage } from './prototypeFlow'

interface InvestigationPlaybackProps {
  active: boolean
  playing: boolean
  stageIndex: number
  stages: PrototypeFlowStage[]
  onStart: () => void
  onPause: () => void
  onReset: () => void
  onClose: () => void
}

export function InvestigationPlayback({ active, playing, stageIndex, stages, onStart, onPause, onReset, onClose }: InvestigationPlaybackProps) {
  if (!active) return <button className="playback-launch" type="button" onClick={onStart}><Play size={14} fill="currentColor" /> Start investigation playback</button>
  const stage = stages[stageIndex]
  return <section className="playback-bar" aria-label="Investigation playback">
    <div><span className="section-kicker">Prototype demo flow</span><strong>{stage.label}</strong><small>{stage.shortLabel} · {stageIndex + 1}/{stages.length}</small></div>
    <div className="playback-actions">{playing ? <button type="button" aria-label="Pause playback" title="Pause playback" onClick={onPause}><Pause size={15} /></button> : <button type="button" aria-label="Resume playback" title="Resume playback" onClick={onStart}><Play size={15} fill="currentColor" /></button>}<button type="button" aria-label="Restart playback" title="Restart playback" onClick={onReset}><RotateCcw size={15} /></button><button type="button" aria-label="Exit playback" title="Exit playback" onClick={onClose}><X size={15} /></button></div>
  </section>
}