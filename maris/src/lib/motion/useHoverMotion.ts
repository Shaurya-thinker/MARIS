import { useRef } from 'react'
import { gsap, useGSAP, isReducedMotion } from './gsap'
import { EASINGS, TIMINGS } from './motionPresets'

/**
 * Micro-interaction hook for tactile press and hover states.
 */
export function useHoverMotion<T extends HTMLElement = HTMLButtonElement>(options?: {
  hoverLiftY?: number
  pressScale?: number
}) {
  const ref = useRef<T>(null)
  const hoverLiftY = options?.hoverLiftY ?? -2
  const pressScale = options?.pressScale ?? 0.98

  useGSAP(
    () => {
      const el = ref.current
      if (!el || isReducedMotion()) return

      const onMouseEnter = () => {
        gsap.to(el, {
          y: hoverLiftY,
          duration: TIMINGS.fast,
          ease: EASINGS.out,
          overwrite: 'auto',
        })
      }

      const onMouseLeave = () => {
        gsap.to(el, {
          y: 0,
          scale: 1,
          duration: TIMINGS.fast,
          ease: EASINGS.out,
          overwrite: 'auto',
        })
      }

      const onMouseDown = () => {
        gsap.to(el, {
          scale: pressScale,
          duration: TIMINGS.instant,
          ease: EASINGS.out,
          overwrite: 'auto',
        })
      }

      const onMouseUp = () => {
        gsap.to(el, {
          scale: 1,
          y: hoverLiftY,
          duration: TIMINGS.fast,
          ease: EASINGS.out,
          overwrite: 'auto',
        })
      }

      el.addEventListener('mouseenter', onMouseEnter)
      el.addEventListener('mouseleave', onMouseLeave)
      el.addEventListener('mousedown', onMouseDown)
      el.addEventListener('mouseup', onMouseUp)

      return () => {
        el.removeEventListener('mouseenter', onMouseEnter)
        el.removeEventListener('mouseleave', onMouseLeave)
        el.removeEventListener('mousedown', onMouseDown)
        el.removeEventListener('mouseup', onMouseUp)
      }
    },
    { scope: ref }
  )

  return ref
}
