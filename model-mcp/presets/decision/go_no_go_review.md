---
name: go_no_go_review
category: decision
mode: single-model
summary: Decide whether the work is ready to proceed, blocked, or needs redesign.
---

# Goal

Make a practical readiness decision instead of allowing vague optimism to substitute for a launch or implementation gate.

# When To Use

- A design or implementation is about to move forward.
- You need a gate review before build, deploy, or launch.
- You want a crisp proceed, block, or redesign decision.

# Prompt

You are performing a go/no-go review for the provided plan, design, or implementation.

Instructions:
- Assess whether the work is ready to proceed now.
- Use three possible outcomes only: Go, No-Go, or Go with Conditions.
- Evaluate readiness across requirements clarity, technical soundness, security, operability, testing, and rollback or recovery planning.
- Identify blockers versus non-blocking concerns. Be strict about the distinction.
- If the plan should not proceed, explain whether the problem is missing information, missing controls, or a flawed direction.
- If the plan can proceed conditionally, list the minimum conditions that must be satisfied first.
- End with the decision, the rationale, and the next actions in priority order.

# Output Expectations

- A single readiness decision.
- A short blocker list and a separate non-blocker list.
- Priority-ordered next actions.
