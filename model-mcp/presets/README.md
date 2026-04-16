# Preset File Format

Each preset lives in its own Markdown file under a category folder.

Use this structure:

```md
---
name: adversarial_design_review
category: critique
mode: single-model
summary: Break an existing design by surfacing hidden assumptions and likely failure modes.
---

# Goal

One short paragraph describing what this preset is trying to accomplish.

# When To Use

- A short list of situations where this preset is appropriate.

# Prompt

The full reusable instruction text that the MCP should prepend before passing user material to the target model.

# Output Expectations

- A short list describing the expected shape of the answer.
```

Rules:

- Keep file names equal to the preset name plus `.md`.
- Use `mode: single-model` for one-provider prompts.
- Use `mode: cross-model` for prompts designed to compare or relay between providers.
- Write prompts so the calling agent only needs to pass task-specific content, not rebuild the framing.
- Prefer concise but strong prompts. The goal is reusable framing with consistent outputs, not maximal verbosity.
