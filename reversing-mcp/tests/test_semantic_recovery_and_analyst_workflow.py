from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from reversing_mcp.app import ReversingMCPApp


def _session_id(payload: dict) -> str:
    return payload["result"]["session"]["session_id"]


def _artifact_id(payload: dict) -> str:
    return payload["result"]["artifact_id"]


def _job_id(payload: dict) -> str:
    return payload["result"]["job_id"]


def _wait_for_job(app: ReversingMCPApp, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = app.get_job(job_id)
        if payload["result"]["status"] in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not complete before timeout.")


def _build_semantic_sample(workspace_root: Path) -> Path:
    source = workspace_root / "feature05_sample.cpp"
    binary = workspace_root / "feature05_sample"
    source.write_text(
        """
        #include <stdio.h>

        struct Config {
            int threshold;
            const char *label;
        };

        static int global_flag = 0;
        static Config g_config = {2, "CFG-LABEL"};

        class Base {
        public:
            virtual int run(int x) { return x + 1; }
            virtual ~Base() = default;
        };

        class Derived : public Base {
        public:
            int run(int x) override { return x * 2; }
        };

        static int inc(int x) { return x + 1; }
        static int dec(int x) { return x - 1; }

        using Fn = int (*)(int);
        static Fn jump_table[2] = {inc, dec};

        int choose_path(int idx, int value) {
            if (idx < 0 || idx > 1) {
                return -1;
            }
            return jump_table[idx](value);
        }

        int score_value(int argc, int value) {
            int local = value + 7;
            if (argc > g_config.threshold) {
                global_flag = 1;
            }
            return local;
        }

        int maybe_throw(int x) {
            try {
                if (x == 13) {
                    throw x;
                }
            } catch (...) {
                return -13;
            }
            return x;
        }

        int main(int argc, char **argv) {
            Derived derived;
            int chosen = choose_path(argc & 1, 10);
            int scored = score_value(argc, chosen);
            int value = derived.run(scored);
            if (global_flag) {
                puts(g_config.label);
            }
            return maybe_throw(value);
        }
        """,
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-g", "-O0", "-fno-inline", str(source), "-o", str(binary)],
        check=True,
        cwd=workspace_root,
    )
    return binary


def test_semantic_queries_workflow_and_batch_operations(tmp_path: Path) -> None:
    sample = _build_semantic_sample(tmp_path)

    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("feature05"))
    artifact_id = _artifact_id(app.add_artifact(session_id, str(sample)))

    started = app.start_artifact_analysis(session_id, artifact_id)
    finished = _wait_for_job(app, _job_id(started))
    assert finished["result"]["status"] == "completed"

    choose_path = app.list_artifact_functions(session_id, artifact_id, query="choose_path")["result"]["items"][0]
    score_value = app.list_artifact_functions(session_id, artifact_id, query="score_value")["result"]["items"][0]
    main_fn = app.list_artifact_functions(session_id, artifact_id, query="main")["result"]["items"][0]

    call_graph = app.get_call_graph(session_id, artifact_id, function_id=main_fn["function_id"], depth=2)
    assert call_graph["ok"] is True
    assert any(edge["target_name"] and "score_value" in edge["target_name"] for edge in call_graph["result"]["edges"])

    cfg = app.get_control_flow_graph(session_id, artifact_id, function_id=main_fn["function_id"])
    assert cfg["ok"] is True
    assert cfg["result"]["control_flow_graph"]["nodes"]
    assert cfg["result"]["control_flow_graph"]["edges"]

    variables = app.get_function_variables(session_id, artifact_id, function_id=score_value["function_id"])
    assert variables["ok"] is True
    assert variables["result"]["variables"]["arguments"]
    assert variables["result"]["variables"]["locals"]

    stack_frame = app.get_stack_frame(session_id, artifact_id, function_id=score_value["function_id"])
    assert stack_frame["ok"] is True
    assert stack_frame["result"]["stack_frame"]["slots"]

    constants = app.get_constant_propagation(session_id, artifact_id, function_id=score_value["function_id"])
    assert constants["ok"] is True
    assert any(item["value"] == 7 for item in constants["result"]["constant_propagation"]["immediates"])

    type_information = app.get_type_information(session_id, artifact_id)
    assert type_information["ok"] is True
    assert type_information["result"]["type_information"]["function_signatures"]

    recovered_types = app.recover_types(session_id, artifact_id)
    assert recovered_types["ok"] is True
    assert recovered_types["result"]["recovered_types"]["items"]

    data_segments = app.inspect_data_segments(session_id, artifact_id)
    assert data_segments["ok"] is True
    assert data_segments["result"]["data_segments"]["typed_views"]

    indirect_flows = app.get_indirect_flows(session_id, artifact_id, function_id=choose_path["function_id"])
    assert indirect_flows["ok"] is True
    assert indirect_flows["result"]["indirect_flows"]["items"]

    exception_metadata = app.get_exception_metadata(session_id, artifact_id)
    assert exception_metadata["ok"] is True
    assert exception_metadata["result"]["exception_metadata"]["available"] is True

    calling_convention = app.get_calling_convention(session_id, artifact_id, function_id=main_fn["function_id"])
    assert calling_convention["ok"] is True
    assert calling_convention["result"]["calling_convention"]["name"]

    ir = app.get_intermediate_representation(session_id, artifact_id, function_id=score_value["function_id"], limit_blocks=4, limit_statements=8)
    assert ir["ok"] is True
    assert ir["result"]["intermediate_representation"]["blocks"]

    runtime_metadata = app.get_runtime_metadata(session_id, artifact_id)
    assert runtime_metadata["ok"] is True
    assert any(item["language"] == "c++" for item in runtime_metadata["result"]["runtime_metadata"]["languages"])

    data_slice = app.slice_data_flow(session_id, artifact_id, function_id=score_value["function_id"], radius=4)
    assert data_slice["ok"] is True
    assert data_slice["result"]["slice"]["items"]

    syscalls = app.identify_system_calls(session_id, artifact_id, function_id=main_fn["function_id"])
    assert syscalls["ok"] is True
    assert isinstance(syscalls["result"]["system_calls"], list)

    neighborhood = app.navigate_neighborhood(session_id, artifact_id, function_id=main_fn["function_id"], depth=2, radius=2)
    assert neighborhood["ok"] is True
    assert neighborhood["result"]["neighborhood"]["callees"]

    prioritized = app.prioritize_functions(session_id, artifact_id, min_score=10, limit=10)
    assert prioritized["ok"] is True
    assert prioritized["result"]["items"]

    classified = app.classify_functions(session_id, artifact_id, include_tags=["control_flow"], limit=10)
    assert classified["ok"] is True
    assert classified["result"]["items"]

    bookmark = app.save_workflow_item(
        session_id,
        "bookmark",
        {"kind": "function", "object_id": choose_path["function_id"]},
        {"label": "check jump table"},
    )
    assert bookmark["ok"] is True

    workflow_items = app.list_workflow_items(session_id, kind="bookmark")
    assert workflow_items["ok"] is True
    assert workflow_items["result"]["items"]

    export_path = tmp_path / "exports" / "curated.json"
    curated = app.export_curated_analysis(
        session_id,
        artifact_id,
        function_ids=[main_fn["function_id"], choose_path["function_id"]],
        output_path=str(export_path),
    )
    assert curated["ok"] is True
    assert export_path.exists()
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert exported["functions"]

    batch = app.batch_query_artifacts(session_id, "prioritize_functions", min_score=10, limit=5)
    assert batch["ok"] is True
    assert batch["result"]["items"][0]["status"] == "completed"
