// Extends the same real page harness; no duplicate resource/search system.
process.env.FEEDBACK_ACCEPTANCE_PHASE = '8c'
await import('./test-recommendation-feedback.mjs')
