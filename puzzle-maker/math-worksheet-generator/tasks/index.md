# Task Breakdown

The design breaks cleanly into the following implementation order:

1. `01-core-data-model.md`
2. `02-reward-content-flow.md`
3. `03-problem-generation.md`
4. `04-mapping-and-validation.md`
5. `05-rendering-and-export.md`
6. `06-testing-and-dev-workflow.md`
7. `07-styled-image-feature-overview.md`
8. `08-styling-settings-and-persistence.md`
9. `09-styling-prompt-and-theme-catalog.md`
10. `10-gemini-image-styling-service.md`
11. `11-foreground-compositing.md`
12. `12-style-verification-and-retry.md`
13. `13-job-flow-and-gallery-integration.md`
14. `14-styling-tests-and-documentation.md`
15. `15-background-worksheet-generation.md`
16. `16-run-state-machine-and-phase-model.md`
17. `17-detail-view-and-auto-open-flow.md`
18. `18-styling-retry-and-recovery.md`
19. `19-stale-job-cleanup-and-timeouts.md`
20. `20-workflow-alignment-tests-and-docs.md`

The styling tasks extend the current worksheet generator with an optional post-render image enhancement stage. That stage must preserve all worksheet semantics, treat Gemini as a background-decoration tool rather than a layout engine, and remain safe to disable when `GEMINI_API_KEY` is not present.

The workflow-alignment tasks compare the current worksheet product to the reference word-search application and pull over the parts that improve reliability and clarity: background generation, a single visible run lifecycle, explicit generation phases, clearer completion handoff, retryable styling, and better stale-job recovery. These tasks should preserve the worksheet-specific improvements already in place, especially the plain-render review checkpoint and semantic foreground compositing.
