import { useRef } from 'react'
import { gsap, useGSAP, isReducedMotion } from './gsap'
import { EASINGS, TIMINGS } from './motionPresets'

/**
 * Custom hook to run a smooth, restrained page transition when navigating between views.
 *
 * @param dependency Typically activeView or a key indicating page change
 * @returns ref to attach to the page wrapper container
 */
export function usePageTransition<T extends HTMLElement = HTMLDivElement>(dependency?: unknown) {
  const containerRef = useRef<T>(null)

  useGSAP(
    () => {
      const el = containerRef.current
      if (!el) return

      if (isReducedMotion()) {
        gsap.set(el, { opacity: 1, y: 0 })
        return
      }

      // Fast, smooth Apple-precision page enter
      gsap.fromTo(
        el,
        { opacity: 0, y: 8 },
        {
          opacity: 1,
          y: 0,
          duration: TIMINGS.page,
          ease: EASINGS.out,
          clearProps: 'transform,opacity',
        }
      )
    },
    { dependencies: [dependency], scope: containerRef }
  )

  return containerRef
}
