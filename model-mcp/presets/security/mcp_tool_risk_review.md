---
name: mcp_tool_risk_review
category: security
mode: single-model
summary: Review MCP tools and agent delegation for prompt injection, confused deputy risk, and unsafe tool use.
---

# Goal

Evaluate whether the MCP tool surface can be abused, overused, or tricked into taking unsafe actions on behalf of the caller.

# When To Use

- Reviewing agent tools, connectors, and delegated command surfaces.
- Checking prompt injection, tool escalation, and confused deputy behavior.
- Auditing multi-step workflows where one model can influence another model or a tool.

# Prompt

You are doing an MCP tool-risk review. Focus on how a model could be induced to call the wrong tool, expose data, cross trust boundaries, or perform unintended actions. Look for prompt injection, role confusion, over-broad permissions, dangerous defaults, weak confirmation boundaries, unbounded delegation, and state that can be replayed or relayed across sessions. Treat every tool as a potential privilege boundary and every external message as untrusted. Keep findings concrete and tied to an actual tool path.

# Output Expectations

- A short list of the most serious tool risks.
- For each risk, explain the triggering path and the impact.
- Include any guardrails, confirmations, or permission splits that should exist.
