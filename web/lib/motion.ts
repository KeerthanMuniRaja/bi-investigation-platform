/* Shared motion language: one set of springs and entrances everywhere. */
import type { Transition, Variants } from "motion/react";

export const spring: Transition = { type: "spring", stiffness: 420, damping: 34, mass: 0.8 };
export const softSpring: Transition = { type: "spring", stiffness: 260, damping: 28 };
export const quick: Transition = { duration: 0.18, ease: [0.2, 0.8, 0.2, 1] };

/** container that staggers its children in */
export const stagger = (gap = 0.035, delay = 0): Variants => ({
  hidden: {},
  show: { transition: { staggerChildren: gap, delayChildren: delay } },
});

export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: softSpring },
};

export const fadeIn: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.25 } },
};

export const slideRight: Variants = {
  hidden: { opacity: 0, x: 14 },
  show: { opacity: 1, x: 0, transition: softSpring },
};

/** height: 0 <-> auto for accordions */
export const collapse: Variants = {
  closed: { height: 0, opacity: 0, transition: { height: { duration: 0.22, ease: [0.2, 0.8, 0.2, 1] }, opacity: { duration: 0.12 } } },
  open: { height: "auto", opacity: 1, transition: { height: { duration: 0.28, ease: [0.2, 0.8, 0.2, 1] }, opacity: { duration: 0.2, delay: 0.05 } } },
};
