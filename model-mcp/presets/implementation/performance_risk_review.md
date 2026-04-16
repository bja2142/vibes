---
name: performance_risk_review
category: implementation
mode: single-model
summary: Find latency, throughput, memory, fan-out, and scaling risks in a design or implementation.
---

# Goal

Evaluate whether the design is likely to stay fast and efficient under realistic load. Focus on bottlenecks, expensive paths, and scaling cliffs.

# When To Use

- Reviewing a system that may need to handle higher traffic or larger inputs.
- Checking for avoidable latency, memory growth, or excessive fan-out.
- Comparing implementation choices with performance in mind.

# Prompt

Review the material as a performance skeptic. Look for hot paths, unnecessary work, repeated calls, synchronous blocking, unbounded fan-out, large payloads, and memory pressure. Prioritize issues by likely user impact and operational cost. Keep the response compact and specific. Do not speculate about micro-optimizations unless the design already has a clear bottleneck. If performance looks acceptable, name the main remaining risks and what would need measurement.

# Output Expectations

- Call out the biggest performance risks first.
- For each risk, explain the likely symptom and why it matters.
- End with the measurements or benchmarks that would de-risk the design.
