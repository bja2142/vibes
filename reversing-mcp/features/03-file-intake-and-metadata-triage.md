# Feature 03: File Intake and Metadata Triage

## Goal
Deliver the first useful binary triage layer: identify what a file is, how it is laid out, and what static metadata can be extracted cheaply.

## Execute After
- `01-foundation-result-model-and-session-core.md`
- `02-security-and-workspace-hardening.md`

## Enables
- `04-core-disassembly-and-analysis.md`
- `06-signatures-extraction-and-obfuscation.md`

## Implementation Tasks
1. Implement file typing for architecture, endianness, bitness, platform, and container or binary format.
2. Implement ranked binary taxonomy classification with evidence and confidence.
3. Add cryptographic hashing and similarity-oriented hashing, including MD5, SHA1, SHA256, `ssdeep`, and `imphash` where applicable.
4. Implement header parsing for entry points, image base, section or segment layout, permissions, subsystem, relocations, and related metadata.
5. Implement segment/section discrepancy analysis for stripped tables, overlap, and non-standard alignments.
6. Implement address translation between file offsets, virtual addresses, RVAs, sections, and segments.
7. Implement string extraction with addresses, encoding metadata, and section context.
8. Implement security-mitigation checks for NX or DEP, ASLR or PIE, stack canaries, RELRO, CFG, and format-specific equivalents.
9. Implement certificate and signature analysis for PE, Mach-O, and ELF build-id metadata where present.
10. Add relocatable object-file handling for unresolved relocations and section-relative addressing.
11. Add raw firmware and headerless binary support with agent-supplied base address, architecture, endianness, and memory-map hints.
12. Implement format-specific deep inspection for PE, ELF, and Mach-O features called out in the requirements.
13. Implement container and child-artifact mapping for archives, fat binaries, installers, and firmware containers.
14. Add opt-in external enrichment hooks that remain disabled by default and clearly label external results.
15. Ensure all list-style results expose deterministic ordering, filtering, `limit`, and cursor behavior where result sets can grow large.

## Deliverables
- File triage toolset for metadata and structure.
- Address-conversion utility layer.
- String and signature metadata extraction layer.
- Optional enrichment integration points behind capability flags.

## Acceptance Criteria
- A newly ingested file can be typed and summarized without invoking deep analysis.
- Triage output includes format, architecture, layout, hashes, strings, and security mitigations in structured JSON.
- Headerless or relocatable inputs can be analyzed with explicit hints instead of failing as unknown formats.
- Nested containers expose child artifacts and offsets without losing provenance.
- External enrichment is opt-in, visibly external, and not required for normal operation.

## Requirements Covered
- §0 Pagination and Truncation Controls
- §0 Deterministic Ordering and Filtering
- §2 File Typing
- §2 Binary Taxonomy
- §2 Hashing
- §2 Header Parsing
- §2 Segment and Section Discrepancy Analysis
- §2 Address Translation
- §2 String Extraction
- §2 Security Mitigations Check
- §2 Certificate and Signature Analysis
- §2 Relocatable Object File Support
- §2 Firmware and Headerless Binary Support
- §2 Format-Specific Deep Inspection
- §2 Container and Child Artifact Mapping
- §2 Optional External Enrichment

## Notes for the Implementing Agent
- Keep this feature cheap and triage-oriented; do not block on full framework analysis.
- Reuse the resource and path controls from Feature 02 for all derived outputs.
