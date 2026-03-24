from __future__ import annotations

import json
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

from reversing_mcp.app import ReversingMCPApp
import reversing_mcp.signatures as signatures


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


def _build_signature_sample(workspace_root: Path) -> tuple[Path, Path, Path]:
    source = workspace_root / "feature06_sample.cpp"
    binary = workspace_root / "feature06_sample"
    overlay_binary = workspace_root / "feature06_overlay"
    archive_path = workspace_root / "feature06_payload.zip"
    source.write_text(
        """
        #include <stdio.h>
        #include <string.h>

        static const char *banner = "FEATURE06-BANNER";
        static const char *encoded_base64 = "U0VDUkVUX01FU1NBR0U=";
        static const char *encoded_hex = "48656c6c6f20576f726c64";
        static const unsigned tea_delta = 0x9e3779b9u;

        __attribute__((noinline)) unsigned mix(unsigned value) {
            return value + tea_delta;
        }

        int main() {
            puts(banner);
            return (int)mix((unsigned)strlen(encoded_base64) + (unsigned)strlen(encoded_hex));
        }
        """,
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-g", "-O0", "-fno-inline", str(source), "-o", str(binary)],
        check=True,
        cwd=workspace_root,
    )
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("docs/readme.txt", b"overlay hello")
        archive.writestr("nested/config.txt", b"embedded config")
    overlay_binary.write_bytes(binary.read_bytes() + archive_path.read_bytes())
    return binary, overlay_binary, archive_path


def test_signatures_extraction_and_obfuscation_workflow(tmp_path: Path) -> None:
    binary, overlay_binary, archive_path = _build_signature_sample(tmp_path)

    app = ReversingMCPApp(workspace_root=tmp_path)
    source_session = _session_id(app.create_session("feature06-source"))
    target_session = _session_id(app.create_session("feature06-target"))
    overlay_artifact = _artifact_id(app.add_artifact(source_session, str(overlay_binary)))
    zip_artifact = _artifact_id(app.add_artifact(source_session, str(archive_path)))

    yara_scan = app.scan_with_yara(source_session, overlay_artifact)
    assert yara_scan["ok"] is True
    assert yara_scan["result"]["total_matches"] >= 1

    fingerprint = app.fingerprint_compiler_toolchain(source_session, overlay_artifact)
    assert fingerprint["ok"] is True
    assert any(item["compiler"] == "gcc" for item in fingerprint["result"]["fingerprints"]["matches"])

    packer = app.detect_packer(source_session, overlay_artifact)
    assert packer["ok"] is True
    assert packer["result"]["packer_detection"]["overlay"]["present"] is True

    entropy = app.calculate_entropy(source_session, overlay_artifact)
    assert entropy["ok"] is True
    assert entropy["result"]["entropy"]["whole_file"]["entropy"] > 0
    assert entropy["result"]["entropy"]["sections"]

    overlay_bytes = app.read_artifact_bytes(
        source_session,
        overlay_artifact,
        "file_offset",
        binary.stat().st_size,
        8,
    )
    assert overlay_bytes["ok"] is True
    assert overlay_bytes["result"]["bytes_hex"].startswith("504b0304")

    decoded = app.deobfuscate_strings(source_session, overlay_artifact, limit=10)
    assert decoded["ok"] is True
    assert decoded["result"]["deobfuscated_strings"]["backend"] == "heuristic-fallback"
    decoded_values = {item["decoded_value"] for item in decoded["result"]["deobfuscated_strings"]["items"]}
    assert "SECRET_MESSAGE" in decoded_values
    assert "Hello World" in decoded_values

    started = app.start_artifact_analysis(source_session, overlay_artifact)
    finished = _wait_for_job(app, _job_id(started))
    assert finished["result"]["status"] == "completed"

    crypto = app.detect_crypto_constants(source_session, overlay_artifact)
    assert crypto["ok"] is True
    assert any(item["name"] == "tea_delta" for item in crypto["result"]["crypto_constants"]["items"])

    libraries = app.recognize_library_code(source_session, overlay_artifact)
    assert libraries["ok"] is True
    assert any(item["library"] == "libc" for item in libraries["result"]["library_recognition"]["libraries"])

    carved = app.carve_embedded_artifacts(source_session, overlay_artifact, attach_to_session=True)
    assert carved["ok"] is True
    assert carved["result"]["items"]
    assert carved["result"]["attached_artifacts"]

    parent_relationships = app.get_artifact_relationships(source_session, overlay_artifact)
    assert parent_relationships["ok"] is True
    assert parent_relationships["result"]["children"]

    related_scan = app.scan_with_yara(source_session, overlay_artifact, include_related=True)
    assert related_scan["ok"] is True
    assert len(related_scan["result"]["items"]) >= 2

    extracted = app.extract_resources(
        source_session,
        zip_artifact,
        attach_to_session=True,
        target_session_id=target_session,
    )
    assert extracted["ok"] is True
    assert extracted["result"]["items"]
    assert extracted["result"]["attached_artifacts"]

    target_artifacts = app.list_artifacts(target_session)
    assert target_artifacts["ok"] is True
    assert target_artifacts["result"]["items"]
    assert all(item["relationship"]["parent_artifact_id"] == zip_artifact for item in target_artifacts["result"]["items"])


def test_deobfuscate_strings_prefers_floss_when_available(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ-test")

    floss_payload = {
        "strings": {
            "decoded_strings": [
                {
                    "address": 4198400,
                    "address_type": "GLOBAL",
                    "string": "decoded via floss",
                    "encoding": "ASCII",
                    "decoded_at": 4198500,
                    "decoding_routine": 4198600,
                }
            ],
            "stack_strings": [
                {
                    "function": 4198400,
                    "string": "stack via floss",
                    "encoding": "ASCII",
                    "program_counter": 4198410,
                    "stack_pointer": 0,
                    "original_stack_pointer": 0,
                    "offset": 0,
                    "frame_offset": 0,
                }
            ],
            "tight_strings": [],
        }
    }

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=json.dumps(floss_payload), stderr="")

    monkeypatch.setattr(signatures.subprocess, "run", fake_run)

    result = signatures.deobfuscate_strings(
        path=sample,
        parsed={"file_type": {"format": "PE"}},
        strings=[{"value": "U0VDUkVUX01FU1NBR0U=", "string_id": "s1", "address": 4096}],
        limit=10,
    )

    assert result["backend"] == "flare-floss+heuristic-fallback"
    assert result["supported_by_floss"] is True
    assert result["errors"] == []
    decoded_values = {item["decoded_value"] for item in result["items"]}
    assert "decoded via floss" in decoded_values
    assert "stack via floss" in decoded_values
    assert "SECRET_MESSAGE" in decoded_values
