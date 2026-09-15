import React, { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertOctagon, RotateCcw } from 'lucide-react'

export interface WorkspaceErrorBoundaryProps {
  children: ReactNode
  onReset?: () => void
}

interface WorkspaceErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

export class WorkspaceErrorBoundary extends Component<
  WorkspaceErrorBoundaryProps,
  WorkspaceErrorBoundaryState
> {
  public state: WorkspaceErrorBoundaryState = {
    hasError: false,
    error: null,
  }

  public static getDerivedStateFromError(error: Error): WorkspaceErrorBoundaryState {
    return { hasError: true, error }
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('WorkspaceErrorBoundary caught render error:', error, errorInfo)
  }

  public handleReset = () => {
    this.setState({ hasError: false, error: null })
    this.props.onReset?.()
  }

  public render() {
    if (this.state.hasError) {
      return (
        <div
          className="workspace-error-boundary"
          role="alert"
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '3rem 2rem',
            textAlign: 'center',
            minHeight: '350px',
            backgroundColor: '#0f172a',
            borderRadius: '8px',
            margin: '1.5rem',
            border: '1px solid #dc2626',
          }}
        >
          <AlertOctagon size={48} color="#ef4444" style={{ marginBottom: '1rem' }} />
          <h2 style={{ fontSize: '1.25rem', fontWeight: 600, color: '#f8fafc', marginBottom: '0.5rem' }}>
            Workspace Encountered a Render Error
          </h2>
          <p style={{ color: '#94a3b8', maxWidth: '520px', fontSize: '0.9rem', marginBottom: '1.25rem' }}>
            An unexpected error occurred while rendering the workspace. Normal API errors are shown within their respective panels; this boundary catches unexpected component exceptions.
          </p>
          {this.state.error && (
            <pre
              style={{
                backgroundColor: '#1e293b',
                color: '#fca5a5',
                padding: '0.75rem 1rem',
                borderRadius: '6px',
                maxWidth: '650px',
                width: '100%',
                overflowX: 'auto',
                fontSize: '0.8rem',
                textAlign: 'left',
                marginBottom: '1.5rem',
                border: '1px solid #334155',
              }}
            >
              {this.state.error.message || String(this.state.error)}
            </pre>
          )}
          <button
            type="button"
            className="primary-button"
            onClick={this.handleReset}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.5rem',
              padding: '0.5rem 1.25rem',
              cursor: 'pointer',
            }}
          >
            <RotateCcw size={15} /> Reset Workspace
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
