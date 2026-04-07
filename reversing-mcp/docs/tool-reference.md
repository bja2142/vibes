# Tool Reference

This reference groups the MCP surface by workflow. Parameter names below use the exact public tool arguments.

## Discovery And Runtime

### `describe_tools`

- Purpose: list tool names, descriptions, prerequisites, and parameter summaries
- Prerequisites: none

### `get_capabilities`

- Purpose: report current server capabilities, tool dependencies, transports, and runtime policy summary
- Prerequisites: none
- Includes: HTTP auth mode, per-tenant and per-agent quotas, and advertised single-agent session isolation rules for streamable HTTP

### `get_runtime_policies`

- Purpose: report workspace root, parser isolation, and configured resource limits
- Prerequisites: none

### `run_parser_probe(path, simulate?)`

- Purpose: validate isolated worker startup and path policy against a real file
- Prerequisites: none

## Sessions And Artifacts

### `create_session(name, description?, settings?)`

- Purpose: create a persisted session
- HTTP behavior: new HTTP sessions are owned by the current tenant and leased to the current agent

### `load_session(session_id?, name?)`

- Purpose: load a session by ID or unique name
- HTTP behavior: a different HTTP agent cannot load a leased session

### `list_sessions(cursor?, limit?)`

- Purpose: enumerate sessions
- HTTP behavior: only sessions visible to the current tenant and agent are listed

### `destroy_session(session_id?, name?)`

- Purpose: remove a session and its persisted state

### `update_session_settings(session_id, settings_patch)`

- Purpose: merge session settings

### `add_artifact(session_id, path, display_name?)`

- Purpose: register a file inside a session

### `list_artifacts(session_id, cursor?, limit?)`

- Purpose: enumerate session artifacts

### `remove_artifact(session_id, artifact_id?, display_name?)`

- Purpose: detach an artifact from a session

### `patch_artifact_bytes(session_id, artifact_id, input_kind, value, bytes_hex, output_path?, attach_to_session?, display_name?)`

- Purpose: apply a byte patch and save the patched artifact back into the workspace
- Notes: reports warnings for decoded instructions, relocations, and sensitive sections when possible

### `patch_artifact_assembly(session_id, artifact_id, input_kind, value, assembly, isa, output_path?, attach_to_session?, display_name?)`

- Purpose: assemble and apply a patch for a supported ISA
- Current ISA support: compact built-in backends for `x86`, `x86_64`, `aarch64`, `arm`, and `thumb`
- Examples by ISA:
  `x86`/`x86_64`: `nop`, `ret`, `int3`, `jmp`, `call`
  `aarch64`: `nop`, `ret`, `brk`, `b`, `bl`
  `arm`: `nop`, `ret`, `bkpt`, `b`, `bl`
  `thumb`: `nop`, `ret`, `bkpt`, `b`

### `find_code_caves(session_id, artifact_id, min_size?)`

- Purpose: locate zero- or filler-byte runs that look suitable for patch stubs or data caves

### `edit_artifact_metadata(session_id, artifact_id, edit_kind, target, value)`

- Purpose: persist manual overrides for function names, function types, variables, globals, named types, and calling conventions

### `import_type_definitions(session_id, artifact_id, source_format, source_text)`

- Purpose: import C-header or structured JSON type definitions into artifact-local override state

### `export_command_log(session_id, format?, output_path?)`

- Purpose: export the persisted Feature 07 command log as JSON or text
- Output metadata: includes non-truncating format metadata for inline and file exports

### `export_analysis_report(session_id, artifact_id, format?, output_path?)`

- Purpose: export a compact artifact report as JSON or human-readable text

### `list_artifact_dependencies(session_id, artifact_id)`

- Purpose: summarize dependency hints from imports, linkage metadata, and artifact relationships

### `correlate_session_artifacts(session_id, artifact_ids?, cursor?, limit?)`

- Purpose: correlate artifacts in one session by shared imports, strings, and recovered function names
- Pagination: deterministic cursor and limit controls over the correlation set

### `diff_artifacts(session_id, left_artifact_id, right_artifact_id)`

- Purpose: compare two artifacts structurally and, when available, by recovered functions and strings

## Composite Brief Workflows

These tools are designed to reduce token usage and tool round trips. They preserve stable IDs but return bounded previews by default.

Shared controls:

- `verbosity`: `brief`, `normal`, or `deep`
- `token_budget_hint`: optional budget hint that can automatically clamp detail
- `include_next_actions`: include or suppress MCP follow-up suggestions
- `include_raw_sections`: opt in to bounded raw previews

### `ingest_and_triage_artifact(session_id, path, display_name?, hints?, analyze?, verbosity?, token_budget_hint?, include_next_actions?, include_raw_sections?)`

- Purpose: attach an artifact, triage it, and optionally queue analysis in one call

### `analyze_and_summarize(session_id, artifact_id, focus?, wait_timeout_seconds?, verbosity?, token_budget_hint?, include_next_actions?, include_raw_sections?)`

- Purpose: start analysis when needed, optionally wait for completion, and return a compact artifact brief
- Focus presets: `general`, `malware`, `patching`, `diffing`, `firmware`, `extraction`

### `hunt_interesting_regions(session_id, artifact_id, objective?, limit?, verbosity?, token_budget_hint?, include_next_actions?, include_raw_sections?)`

- Purpose: return a ranked shortlist of likely-interesting functions, strings, imports, and static hints

### `trace_capability(session_id, artifact_id, target, depth?, verbosity?, token_budget_hint?, include_next_actions?, include_raw_sections?)`

- Purpose: expand one function target into neighborhood, xrefs, variables, and instruction context
- `target`: JSON object using `function_id`, `name`, or `address`

### `prepare_patch_plan(session_id, artifact_id, objective, target?, min_code_cave_size?, verbosity?, token_budget_hint?, include_next_actions?, include_raw_sections?)`

- Purpose: summarize patchability, code caves, and candidate patch points in one response

### `artifact_relationship_brief(session_id, artifact_id, focus?, verbosity?, token_budget_hint?, include_next_actions?, include_raw_sections?)`

- Purpose: summarize parents, children, dependencies, correlation hits, and likely diff candidates
- Focus presets: `general`, `diffing`, `extraction`

## Triage And File Intake

### `triage_artifact(session_id, artifact_id, hints?, string_preview_limit?)`

- Purpose: identify format, layout, mitigations, signatures, taxonomy, and string preview
- Use when: you need fast metadata before analysis

### `list_artifact_strings(session_id, artifact_id, cursor?, limit?, min_length?, encoding?, query?, hints?)`

- Purpose: list extracted strings with filtering

### `translate_artifact_address(session_id, artifact_id, input_kind, value, hints?)`

- Purpose: map file offsets, virtual addresses, and RVAs

### `list_artifact_children(session_id, artifact_id, cursor?, limit?, hints?)`

- Purpose: enumerate child objects for supported container-like formats

### `lookup_external_enrichment(session_id, artifact_id, providers?, opt_in?)`

- Purpose: query the external-enrichment hook state
- Note: currently reports disabled unless a backend is added later

## Signatures, Extraction, And Obfuscation

### `scan_with_yara(session_id, artifact_id, rules_text?, include_related?)`

- Purpose: run YARA if available or the built-in heuristic fallback
- Typical output: one result block per scanned artifact, each with `matches`

### `fingerprint_compiler_toolchain(session_id, artifact_id)`

- Purpose: recover compiler/toolchain hints from strings and metadata

### `detect_packer(session_id, artifact_id)`

- Purpose: report packer-style evidence such as overlays, suspicious entropy, marker strings, and small import surfaces

### `calculate_entropy(session_id, artifact_id)`

- Purpose: calculate whole-file and per-section entropy

### `deobfuscate_strings(session_id, artifact_id, limit?)`

- Purpose: prefer FLARE FLOSS decoded/stack/tight strings for PE artifacts, then fall back to bounded base64 and hex decode candidates when needed
- Partial results: can truncate at `limit`

### `extract_resources(session_id, artifact_id, output_subdir?, attach_to_session?, target_session_id?, analyze_extracted?)`

- Purpose: extract PE resources or container members to the workspace
- Important flags:
  - `attach_to_session`: re-register outputs as artifacts
  - `target_session_id`: attach to a different session
  - `analyze_extracted`: immediately analyze supported binaries

### `carve_embedded_artifacts(session_id, artifact_id, output_subdir?, attach_to_session?, target_session_id?, analyze_extracted?, recurse?)`

- Purpose: carve appended overlays and nested embedded artifacts
- Important behavior:
  - obeys carved-byte and recursion-depth limits
  - preserves provenance
  - can recurse into nested containers

### `get_artifact_relationships(session_id, artifact_id, direction?)`

- Purpose: return parent and child links created by extraction or carving
- `direction`: `parents`, `children`, or `both`

## Analysis And Recovery

### `start_artifact_analysis(session_id, artifact_id, hints?)`

- Purpose: queue asynchronous program analysis

### `get_job(job_id)`

- Purpose: poll job state and read terminal results

### `list_jobs(session_id?, status?, cursor?, limit?)`

- Purpose: enumerate jobs

### `cancel_job(job_id)`

- Purpose: request async job cancellation

### `get_analysis_synopsis(session_id, artifact_id)`

- Purpose: summarize the cached analysis state
- Includes: compact annotation counts, extraction history, recent signature operations, patch history, and outstanding unknowns

### `list_artifact_symbols(session_id, artifact_id, cursor?, limit?, query?, kind?)`

- Purpose: list imports, exports, thunks, and unresolved symbols

### `list_artifact_functions(session_id, artifact_id, cursor?, limit?, query?)`

- Purpose: enumerate recovered functions
- Query behavior: exact and prefix matches rank ahead of loose substring matches

### `get_artifact_instruction_mode(session_id, artifact_id)`

- Purpose: report supported and current instruction mode

### `set_artifact_instruction_mode(session_id, artifact_id, mode)`

- Purpose: override instruction mode when the artifact supports multiple modes

### `disassemble_function(session_id, artifact_id, function_id?, name?, address?, cursor?, limit?, instruction_mode_override?)`

- Purpose: return structured disassembly for one function

### `disassemble_range(session_id, artifact_id, input_kind, start_value, size, cursor?, limit?, instruction_mode_override?)`

- Purpose: disassemble an arbitrary mapped range

### `decompile_function(session_id, artifact_id, function_id?, name?, address?, char_limit?, line_limit?)`

- Purpose: return best-effort pseudo-C

### `read_artifact_bytes(session_id, artifact_id, input_kind, value, length, hints?)`

- Purpose: inspect raw bytes with hex and ASCII views

### `list_artifact_xrefs(session_id, artifact_id, function_id?, string_id?, address?, cursor?, limit?)`

- Purpose: list cross-references to a function, string, or address

### `search_artifact(session_id, artifact_id, kind, query?, start_address?, end_address?, cursor?, limit?, case_sensitive?)`

- Purpose: search names, strings, immediates, opcodes, patterns, and ranges

### `get_artifact_linkage(session_id, artifact_id)`

- Purpose: report imports, exports, PLT/GOT/IAT, and related linkage metadata

### `get_artifact_debug_info(session_id, artifact_id)`

- Purpose: report DWARF/PDB-derived or embedded source metadata

## Semantic Recovery

### `detect_crypto_constants(session_id, artifact_id)`

- Purpose: find known crypto or checksum constants in analysis output and raw bytes

### `recognize_library_code(session_id, artifact_id)`

- Purpose: identify likely library/runtime code and recognized library families

### `get_call_graph(session_id, artifact_id, function_id?, name?, address?, direction?, depth?, limit_nodes?, limit_edges?)`

- Purpose: return bounded incoming and outgoing call graph slices

### `get_control_flow_graph(session_id, artifact_id, function_id?, name?, address?)`

- Purpose: return basic blocks, edges, loops, branch targets, and fallthroughs

### `get_function_variables(session_id, artifact_id, function_id?, name?, address?)`

- Purpose: return recovered arguments, locals, globals, and register parameters

### `get_stack_frame(session_id, artifact_id, function_id?, name?, address?)`

- Purpose: return recovered stack slots and saved-register hints

### `get_constant_propagation(session_id, artifact_id, function_id?, name?, address?)`

- Purpose: return recovered immediates and bounded call-site propagation

### `get_type_information(session_id, artifact_id)`

- Purpose: return function signatures, named types, and typed memory summaries

### `recover_types(session_id, artifact_id)`

- Purpose: return heuristic RTTI, vtables, and related type recoveries

### `inspect_data_segments(session_id, artifact_id)`

- Purpose: inspect non-executable sections for string pools, pointer tables, and typed views

### `get_indirect_flows(session_id, artifact_id, function_id?, name?, address?)`

- Purpose: return unresolved indirect calls and branches

### `get_exception_metadata(session_id, artifact_id)`

- Purpose: return unwind and exception metadata

### `get_calling_convention(session_id, artifact_id, function_id?, name?, address?)`

- Purpose: return detected or inferred calling convention data

### `get_intermediate_representation(session_id, artifact_id, function_id?, name?, address?, limit_blocks?, limit_statements?)`

- Purpose: return bounded backend IR, currently VEX-oriented

### `get_runtime_metadata(session_id, artifact_id)`

- Purpose: return recovered language/runtime hints such as C++, Go, Swift, Objective-C, Rust, or fallback C

### `slice_data_flow(session_id, artifact_id, function_id?, name?, address?, anchor_address?, register?, radius?)`

- Purpose: return a bounded static data-flow slice around an anchor

### `identify_system_calls(session_id, artifact_id, function_id?, name?, address?)`

- Purpose: report raw syscall instructions and bounded syscall-number guesses

### `navigate_neighborhood(session_id, artifact_id, function_id?, name?, address?, depth?, radius?)`

- Purpose: return callers, callees, nearby functions, and nearby strings

### `prioritize_functions(session_id, artifact_id, include_tags?, exclude_tags?, min_score?, limit?)`

- Purpose: return functions sorted by triage score with optional filtering

### `classify_functions(session_id, artifact_id, include_tags?, exclude_tags?, limit?)`

- Purpose: return functions grouped by heuristic classification tags

## Analyst Workflow And State

### `save_workflow_item(session_id, kind, target, value, annotation_id?)`

- Purpose: save bookmarks, named regions, and notes on top of the annotation system

### `list_workflow_items(session_id, kind?, artifact_id?, cursor?, limit?)`

- Purpose: list persisted workflow items

### `register_provisional_function(session_id, artifact_id, name, address?)`

- Purpose: create a temporary function handle before full analysis exists

### `register_provisional_string(session_id, artifact_id, value, address?)`

- Purpose: create a temporary string handle before full analysis exists

### `get_object_reference(session_id, object_id)`

- Purpose: resolve provisional object IDs

### `put_annotation(session_id, target, annotation_type, value, annotation_id?)`

- Purpose: create or update one annotation

### `list_annotations(session_id, artifact_id?, target_kind?, annotation_type?, cursor?, limit?)`

- Purpose: enumerate annotations with filters

### `get_annotation_history(session_id, annotation_id)`

- Purpose: read full annotation revision history

### `revert_annotation(session_id, annotation_id, revision_id?)`

- Purpose: revert an annotation to a prior revision

### `create_session_snapshot(session_id, name, description?)`

- Purpose: capture a whole-session checkpoint

### `list_session_snapshots(session_id)`

- Purpose: list snapshots for a session

### `restore_session_snapshot(session_id, snapshot_id?, name?)`

- Purpose: restore a snapshot in place

### `export_curated_analysis(session_id, artifact_id, function_ids?, string_ids?, annotation_ids?, output_path?)`

- Purpose: write a curated subset of analysis state to disk

### `batch_query_artifacts(session_id, operation, include_tags?, exclude_tags?, min_score?, limit?)`

- Purpose: run an eligible query across every artifact in a session
- Current operations:
  - `analysis_synopsis`
  - `classify_functions`
  - `prioritize_functions`
  - `inspect_data_segments`

### `export_session_state(session_id, output_path?)`

- Purpose: export machine-readable session state

### `start_artifact_reanalysis(session_id, artifact_id)`

- Purpose: queue reanalysis and invalidate generation-scoped function/string IDs on completion

## Ghidra Headless Analysis

### `ghidra_decompile(session_id, artifact_id, address, timeout_seconds?)`

- Purpose: decompile a function using the Ghidra headless decompiler
- Use when: angr decompilation is insufficient for complex binaries, or when higher-quality pseudo-C output is needed
- Prerequisites: `add_artifact`
- Notes: requires Ghidra to be installed in the container (`/opt/ghidra`)

### `ghidra_analyze(session_id, artifact_id, timeout_seconds?)`

- Purpose: run full Ghidra headless analysis and export functions, strings, imports, and sections
- Use when: you want a comprehensive analysis from a second backend to complement angr
- Prerequisites: `add_artifact`

### `run_ghidra_script(session_id, artifact_id, script, timeout_seconds?)`

- Purpose: run a custom Ghidra Python script against an artifact binary
- Use when: you need specialized analysis not covered by the built-in tools
- Notes: script runs in Ghidra's Jython environment with full Ghidra API access

## Cross-Server Bridge

### `export_dynamic_manifest(session_id, artifact_id, output_path?)`

- Purpose: export a JSON manifest of functions, strings, imports, and addresses for use by a dynamic analysis tool
- Use when: handing off from static analysis to dynamic analysis in `pwn-mcp`
- Prerequisites: `start_artifact_analysis` (analysis must be complete)
- Output: JSON file written to the shared workspace volume containing function names/addresses, strings, imports, and artifact metadata
- Companion tool: `import_static_analysis` in `pwn-mcp` reads these manifests

## Notes On Limits And Partial Results

- Extraction and carving may return `partial=true`
- Large deobfuscation candidate sets can truncate at `limit`
- Recursive carving stops at the configured recursion depth
- Derived artifacts stay under the carved-byte and artifact-count budgets
