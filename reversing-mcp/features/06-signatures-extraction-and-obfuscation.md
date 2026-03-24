# Feature 06: Signatures, Extraction, and Obfuscation Handling

## Goal
Add recognition, carving, extraction, and obfuscation-oriented workflows for packed or multi-stage samples.

## Execute After
- `03-file-intake-and-metadata-triage.md`
- `04-core-disassembly-and-analysis.md`
- `05-semantic-recovery-and-analyst-workflow.md`

## Enables
- `07-patching-multi-artifact-and-interop.md`
- `08-transport-ops-and-polish.md`

## Implementation Tasks
1. Implement YARA scanning against original and extracted artifacts.
2. Implement crypto-constant detection with evidence and confidence.
3. Implement library-code recognition using available signature systems.
4. Implement compiler and toolchain fingerprinting.
5. Implement packer detection with signature-based and heuristic methods.
6. Implement entropy calculation for whole files, sections, and extracted regions.
7. Implement static string deobfuscation with explicit method reporting and bounded expectations.
8. Implement resource extraction for PE and equivalent format-specific resource containers.
9. Implement general embedded-artifact carving for overlays, appended blobs, archives, and embedded firmware components.
10. Add decompression-bomb detection and enforce carved-byte, depth, and artifact-count limits from the resource-control layer.
11. Implement recursive analysis handoff so extracted artifacts can be queued into new or existing sessions without manual re-ingestion.
12. Implement artifact-relationship tracking across carving and extraction boundaries, preserving provenance fields from the shared schema.

## Deliverables
- Signature and heuristic-recognition tools.
- Extraction and recursive-carving workflow.
- Provenance-preserving artifact graph for extracted outputs.

## Acceptance Criteria
- YARA, packer, library, compiler, and crypto detections return structured evidence and confidence.
- Extracted artifacts are written safely to the workspace with sanitized filenames and preserved provenance.
- Recursive extraction stops cleanly at configured limits and returns partial results plus information about what was not processed.
- Extracted children can be fed back into analysis sessions without manual file re-registration.

## Requirements Covered
- §6 YARA Scanning
- §6 Crypto Constant Detection
- §6 Library Code Recognition
- §6 Compiler and Toolchain Fingerprinting
- §7 Packer Detection
- §7 Entropy Calculation
- §7 Static String Deobfuscation
- §7 Resource Extraction
- §7 General Embedded Artifact Carving
- §7 Recursive Analysis Handoff
- §7 Artifact Relationship Tracking

## Notes for the Implementing Agent
- This feature depends heavily on the safeguards from Feature 02; do not bypass the central extraction limits.
- Preserve exact byte ranges and container paths in provenance for every carved child.
