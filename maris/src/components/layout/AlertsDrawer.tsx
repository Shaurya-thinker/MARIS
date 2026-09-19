import { Activity, AlertTriangle, CheckCircle2, Info, Radio, ShieldAlert, X } from 'lucide-react'

export interface SystemAlert {
  id: string
  title: string
  message: string
  timestamp: string
  type: 'info' | 'warning' | 'error' | 'success'
}

interface AlertsDrawerProps {
  isOpen: boolean
  onClose: () => void
  alerts?: SystemAlert[]
}

const DEFAULT_ALERTS: SystemAlert[] = [
  {
    id: 'alt-1',
    title: 'Sentinel-1A SAR Ingestion Ready',
    message: 'Copernicus Level-1 GRD product ingested and geometric calibration verified.',
    timestamp: new Date(Date.now() - 1000 * 60 * 12).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    type: 'success',
  },
  {
    id: 'alt-2',
    title: 'Spill Detection Metric Flag',
    message: 'Dark patch anomaly segmented in Sentinel-1 scene with 94% spatial confidence.',
    timestamp: new Date(Date.now() - 1000 * 60 * 28).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    type: 'info',
  },
  {
    id: 'alt-3',
    title: 'Metocean Forcing Integration',
    message: 'Wind and surface current vectors retrieved for 48-hour backward/forward trajectory.',
    timestamp: new Date(Date.now() - 1000 * 60 * 45).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    type: 'info',
  },
  {
    id: 'alt-4',
    title: 'AIS Telemetry Stream',
    message: 'Spatially relevant candidate vessels correlated within temporal search window.',
    timestamp: new Date(Date.now() - 1000 * 60 * 62).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    type: 'info',
  },
]

export function AlertsDrawer({ isOpen, onClose, alerts = DEFAULT_ALERTS }: AlertsDrawerProps) {
  if (!isOpen) return null

  function getAlertIcon(type: SystemAlert['type']) {
    switch (type) {
      case 'success':
        return <CheckCircle2 size={16} color="var(--color-accent)" />
      case 'warning':
        return <AlertTriangle size={16} color="var(--color-warning)" />
      case 'error':
        return <ShieldAlert size={16} color="var(--color-error)" />
      case 'info':
      default:
        return <Info size={16} color="var(--color-info)" />
    }
  }

  return (
    <>
      <div className="alerts-drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <aside className="alerts-drawer" role="dialog" aria-label="System Telemetry Alerts">
        <div className="alerts-drawer-header">
          <div>
            <span className="section-kicker">Live Telemetry</span>
            <h2>System Alerts &amp; Events</h2>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Close alerts">
            <X size={16} />
          </button>
        </div>

        <div className="alerts-drawer-list">
          {alerts.map((alert) => (
            <div className="alert-item" key={alert.id}>
              <div className="alert-item-icon">{getAlertIcon(alert.type)}</div>
              <div className="alert-item-content">
                <strong>{alert.title}</strong>
                <p>{alert.message}</p>
                <span className="alert-item-time">{alert.timestamp} UTC</span>
              </div>
            </div>
          ))}
        </div>

        <div style={{ padding: '0.75rem 1.25rem', borderTop: '1px solid var(--color-border)', background: 'rgba(8, 23, 27, 0.4)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', color: 'var(--color-text-subtle)', fontSize: '0.65rem', fontFamily: 'var(--font-mono)' }}>
            <Radio size={13} color="var(--color-accent)" />
            <span>Telemetry Bus: Active · Port 8000</span>
          </div>
        </div>
      </aside>
    </>
  )
}
