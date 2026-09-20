import gsap from 'gsap'
import { useGSAP } from '@gsap/react'
import { EASINGS, TIMINGS } from './motionPresets'

// Register official GSAP React integration plugin
gsap.registerPlugin(useGSAP)

/**
 * Check if the user has requested reduced motion in their system settings.
 */
export function isReducedMotion(): boolean {
  if (typeof window === 'undefined') return false
  return window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ?? false
}

/**
 * Animate page entrance with reduced motion consideration.
 */
export function animatePageEnter(element: HTMLElement | null, onComplete?: () => void) {
  if (!element) return null
  if (isReducedMotion()) {
    gsap.set(element, { opacity: 1, y: 0 })
    onComplete?.()
    return null
  }

  return gsap.fromTo(
    element,
    { opacity: 0, y: 8 },
    {
      opacity: 1,
      y: 0,
      duration: TIMINGS.page,
      ease: EASINGS.out,
      clearProps: 'transform,opacity',
      onComplete,
    }
  )
}

/**
 * Staggered entrance for lists of cards or telemetry items.
 */
export function animateStaggerCards(
  targets: gsap.DOMTarget,
  scope?: HTMLElement | null
) {
  if (isReducedMotion()) {
    gsap.set(targets, { opacity: 1, y: 0 })
    return null
  }

  return gsap.fromTo(
    targets,
    { opacity: 0, y: 12 },
    {
      opacity: 1,
      y: 0,
      duration: TIMINGS.base,
      ease: EASINGS.out,
      stagger: TIMINGS.stagger,
      clearProps: 'transform,opacity',
      ...(scope ? { scope } : {}),
    }
  )
}

export { gsap, useGSAP }
