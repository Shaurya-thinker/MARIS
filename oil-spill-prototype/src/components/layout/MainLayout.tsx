import type { ReactNode } from 'react'

export function MainLayout({ children }: { children: ReactNode }) {
  return <div className="maris-layout">{children}</div>
}