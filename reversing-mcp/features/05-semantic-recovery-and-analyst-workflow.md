# Feature 05: Semantic Recovery and Analyst Workflow

## Goal
Add the higher-level control-flow, data-flow, typing, annotation, and workflow features that make the MCP useful for deep reverse engineering.

## Execute After
- `04-core-disassembly-and-analysis.md`

## Enables
- `06-signatures-extraction-and-obfuscation.md`
- `07-patching-multi-artifact-and-interop.md`

## Implementation Tasks
1. Implement call-graph queries with incoming and outgoing edges.
2. Implement control-flow-graph queries with basic blocks, branch targets, loops, and fallthrough edges.
3. Implement variable recovery for arguments, locals, globals, and register-based parameters.
4. Implement stack-frame layout as a first-class structured query.
5. Implement constant-propagation queries for instructions and call sites, including partial or ambiguous states.
6. Implement type-information queries for structures, unions, enums, classes, and typed memory.
7. Implement automated type recovery for RTTI, vtables, and class hierarchies with per-item confidence.
8. Implement data-segment inspection for arrays, pointer tables, configuration blobs, and typed memory views.
9. Implement indirect-flow recovery for jump tables, switch statements, virtual dispatch, and unresolved indirect calls.
10. Implement exception and unwind metadata queries.
11. Implement calling-convention query support.
12. Implement intermediate-representation access for supported backends.
13. Implement language-runtime metadata recovery for C++, Go, Objective-C, Swift, and Rust artifacts where supported.
14. Implement bounded static data-flow slicing.
15. Implement system-call identification for raw syscall instructions.
16. Implement recursive navigation and neighborhood queries.
17. Implement non-destructive filtering and prioritization with explicit exclusion mechanisms and confidence.
18. Implement triage scoring with evidence and tunable thresholds.
19. Implement function-classification tagging.
20. Implement bookmarks, named regions, and analysis notes bound to session state.
21. Implement curated artifact export for agent-selected subsets of analysis state.
22. Implement batch operations across all artifacts in a session for eligible queries and actions.

## Deliverables
- Semantic-analysis query layer.
- Navigation and triage primitives.
- Analyst productivity features for notes, bookmarks, and curated exports.
- Batch-operation support built on the session model.

## Acceptance Criteria
- A function can be inspected through CFG, call graph, variables, stack frame, propagated constants, and typed data views.
- Heuristic or incomplete recoveries carry explicit confidence and evidence at the item level.
- Filtering hides noisy functions without removing them from direct query access.
- Analysts can save notes, bookmarks, and curated exports and retrieve them later in the same session.
- Batch operations can run once across all session artifacts instead of forcing client-side iteration.

## Requirements Covered
- §4 Call Graphs
- §4 Control Flow Graphs
- §4 Variable Recovery
- §4 Stack Frame Layout
- §4 Constant Propagation Queries
- §4 Type Information Query
- §4 Automated Type Recovery
- §4 Data Segment Inspection
- §4 Indirect Flow Recovery
- §4 Exception and Unwind Metadata
- §4 Calling Convention Query
- §4 Intermediate Representation Access
- §4 Language Runtime Metadata Recovery
- §4 Static Data-Flow Slicing
- §4 System Call Identification
- §5 Recursive Navigation
- §5 Neighborhood Queries
- §5 Filtering and Prioritization
- §5 Triage Scoring
- §5 Function Classification Tags
- §5 Bookmarks and Named Regions
- §5 Analysis Notes
- §5 Curated Artifact Export
- §1 Batch Operations

## Notes for the Implementing Agent
- Keep all heuristic outputs explicit about ambiguity; omission is worse than a low-confidence result.
- Reuse annotation history from Feature 01 for notes, tags, renames, and other mutable analyst state.
