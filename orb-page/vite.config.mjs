// Builds the floating-orb page (index.html, loaded by bin/audio_helper's orb window) and the first run's window
// (tour.html, bin/onboarding's): the orb and the UI parts in src/ui/, on a transparent page.
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const IS_TOUR = process.env.PAGE === 'tour'

export default defineConfig({
  base: './',
  plugins: [react()],
  // One page per build, so each is a single script inline.py can fold in: the orb (index.html),
  // then PAGE=tour for the first run's window (tour.html), added to the same dist/.
  build: {
    outDir: 'dist',
    emptyOutDir: !IS_TOUR,
    rollupOptions: { input: IS_TOUR ? 'tour.html' : 'index.html' },
  },
})
