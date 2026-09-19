import { Pause, Play, Radio, RotateCcw, X } from 'lucide-react'
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

export function InvestigationPlayback({
  active,
  playing,
  stageIndex,
  stages,
  onStart,
  onPause,
  onReset,
  onClose,
}: InvestigationPlaybackProps) {
  if (!active) {
    return (
      <button className="playback-launch" type="button" onClick={onStart}>
        <Play size={13} fill="currentColor" />
        <span>Start Reconstructed Playback</span>
      </button>
    )
  }

  const stage = stages[stageIndex]
  const progressPercent = Math.round(((stageIndex + 1) / Math.max(stages.length, 1)) * 100)

  return (
    <section className="playback-bar" aria-label="Investigation playback" style={{ borderRadius: 'var(--radius-xs)', position: 'relative', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
        <div className="brand-mark" style={{ width: '1.8rem', height: '1.8rem' }}>
          <Radio size={14} />
        </div>
        <div>
          <span className="section-kicker">Reconstructed Timeline</span>
          <strong style={{ fontSize: '0.82rem' }}>{stage.label}</strong>
          <small style={{ color: 'var(--color-text-muted)' }}>
            {stage.shortLabel} · Step {stageIndex + 1} of {stages.length} ({progressPercent}%)
          </small>
        </div>
      </div>

      <div className="playback-actions">
        {playing ? (
          <button type="button" aria-label="Pause playback" title="Pause playback" onClick={onPause}>
            <Pause size={14} />
          </button>
        ) : (
          <button type="button" aria-label="Resume playback" title="Resume playback" onClick={onStart}>
            <Play size={14} fill="currentColor" />
          </button>
        )}
        <button type="button" aria-label="Restart playback" title="Restart playback" onClick={onReset}>
          <RotateCcw size={14} />
        </button>
        <button type="button" aria-label="Exit playback" title="Exit playback" onClick={onClose}>
          <X size={14} />
        </button>
      </div>

      {/* Subtle bottom progress bar */}
      <div
        style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          height: '2px',
          width: `${progressPercent}%`,
          background: 'var(--color-accent)',
          transition: 'width 300ms ease-out',
        }}
      />
    </section>
  )
}