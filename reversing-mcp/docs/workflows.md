# Common Workflows

## 1. Intake And Triage

Use this when you have a new sample and want to understand its format before deeper analysis.

Recommended sequence:

1. `create_session`
2. `add_artifact`
3. `triage_artifact`
4. `list_artifact_strings`
5. `translate_artifact_address`
6. `list_artifact_children`

Use cases:

- Validate file type and architecture
- Inspect mitigations and signatures
- Preview strings
- Enumerate archive members or Mach-O headers

## 2. Core Code Analysis

Use this when you want to browse functions and inspect behavior.

Recommended sequence:

1. `start_artifact_analysis`
2. `get_job`
3. `get_analysis_synopsis`
4. `list_artifact_functions`
5. `disassemble_function`
6. `decompile_function`
7. `list_artifact_xrefs`
8. `search_artifact`

Use cases:

- Recover the function list
- Read assembly or pseudo-C
- Find callsites and references
- Search for strings or constants

## 3. Semantic Recovery

Use this when plain disassembly is not enough.

Recommended function-focused sequence:

1. `get_call_graph`
2. `get_control_flow_graph`
3. `get_function_variables`
4. `get_stack_frame`
5. `get_constant_propagation`
6. `get_indirect_flows`
7. `slice_data_flow`
8. `navigate_neighborhood`

Artifact-wide semantic sequence:

1. `get_type_information`
2. `recover_types`
3. `inspect_data_segments`
4. `get_exception_metadata`
5. `get_runtime_metadata`
6. `prioritize_functions`
7. `classify_functions`

## 4. Analyst Workflow

Use this to persist reasoning, bookmarks, and exports.

Recommended sequence:

1. `save_workflow_item`
2. `list_workflow_items`
3. `put_annotation`
4. `list_annotations`
5. `create_session_snapshot`
6. `export_curated_analysis`
7. `batch_query_artifacts`

Use cases:

- Keep findings across turns
- Tag artifacts or functions
- Save bookmarks or notes
- Snapshot a known-good state before broad edits or reanalysis
- Export a review package for another tool or agent

## 5. Signature And Obfuscation Review

Use this for packed, suspicious, or multi-stage samples.

Recommended sequence:

1. `scan_with_yara`
2. `fingerprint_compiler_toolchain`
3. `detect_packer`
4. `calculate_entropy`
5. `deobfuscate_strings`
6. `detect_crypto_constants`
7. `recognize_library_code`

Use cases:

- Flag suspicious overlays or packers
- Identify compiler provenance
- Recover simple encoded strings
- Surface crypto or checksum constants
- Distinguish application code from runtime/library code

## 6. Extraction And Recursive Handoff

Use this when the sample contains embedded content or containers.

Recommended sequence:

1. `list_artifact_children`
2. `extract_resources`
3. `carve_embedded_artifacts`
4. `get_artifact_relationships`
5. optionally `start_artifact_analysis` on derived artifacts

Flags that matter:

- `attach_to_session`: register derived outputs as artifacts automatically
- `target_session_id`: attach outputs into another session
- `analyze_extracted`: analyze supported derived binaries immediately
- `recurse`: continue carving nested containers until limits are hit

Expected behavior:

- Partial results when artifact-count, carved-byte, or recursion limits are reached
- Sanitized output names
- Parent-child provenance preserved on attached artifacts

## 7. Reanalysis And State Management

Use this when the artifact changed or you want to refresh cached analysis.

Recommended sequence:

1. `start_artifact_reanalysis`
2. `get_job`
3. refresh `function_id` and `string_id` references
4. optionally `create_session_snapshot` before risky operations

Remember:

- Reanalysis invalidates generation-scoped function and string IDs
- Persisted notes on artifact-level targets remain valid
- Function-level notes should be rechecked after reanalysis

## 8. Patching And Multi-Artifact Review

Use this when you need to prepare a bypass, rename recovered objects, import types, or compare patched and derived artifacts.

Recommended sequence:

1. `find_code_caves`
2. `edit_artifact_metadata`
3. `import_type_definitions`
4. `prepare_patch_plan`
5. `patch_artifact_bytes` or `patch_artifact_assembly`
6. `artifact_relationship_brief`
7. `diff_artifacts`
8. `export_command_log`
9. `export_analysis_report`

Use cases:

- Plan a control-flow bypass before mutating bytes
- Persist analyst naming and calling-convention overrides
- Import headers to make downstream summaries more legible
- Compare original and patched artifacts without manually reconstructing relationships
- Export a compact audit trail for another agent or tool

## 9. Token-Efficient Composite Briefs

Use this when you want fewer MCP round trips and bounded responses that still preserve stable IDs for follow-up.

Recommended sequences:

1. Intake shortcut:
   `ingest_and_triage_artifact`
   then `analyze_and_summarize`
2. Hunting shortcut:
   `hunt_interesting_regions`
   then `trace_capability`
3. Patching shortcut:
   `analyze_and_summarize(focus=patching)`
   then `prepare_patch_plan`
4. Multi-artifact shortcut:
   `artifact_relationship_brief`

Useful presets:

- `focus=malware`: compact strings, crypto hints, library hints
- `focus=patching`: instruction-mode and code-cave oriented summary
- `focus=diffing`: relationship and comparison-oriented summary
- `focus=firmware`: format/layout-oriented summary

Budget controls:

- `verbosity=brief|normal|deep`
- `token_budget_hint` to clamp payload size
- `include_raw_sections=true` only when you need bounded raw previews
