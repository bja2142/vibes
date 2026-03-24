from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from reversing_mcp.app import ReversingMCPApp


def _session_id(payload: dict) -> str:
    return payload["result"]["session"]["session_id"]


def _artifact_id(payload: dict) -> str:
    return payload["result"]["artifact_id"]


def test_triage_elf_artifact_includes_layout_hashes_strings_and_mitigations(tmp_path: Path) -> None:
    sample = tmp_path / "ls.bin"
    shutil.copy("/bin/ls", sample)

    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("triage-elf"))
    artifact_id = _artifact_id(app.add_artifact(session_id, str(sample)))

    payload = app.triage_artifact(session_id, artifact_id, string_preview_limit=5)

    assert payload["ok"] is True
    result = payload["result"]
    assert result["file_type"]["format"] == "ELF"
    assert result["file_type"]["architecture"]
    assert result["hashes"]["sha256"]
    assert "sections" in result["layout"]
    assert len(result["layout"]["sections"]) > 0
    assert "nx" in result["security_mitigations"]
    assert "elf_build_id" in result["signatures"]
    assert result["strings_preview"]["total"] >= len(result["strings_preview"]["items"])


def test_raw_hinted_artifact_supports_string_listing_and_address_translation(tmp_path: Path) -> None:
    sample = tmp_path / "firmware.bin"
    sample.write_bytes(b"boot\x00loader\x00h\x00i\x00!\x00\x00\x00")

    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("triage-raw"))
    artifact_id = _artifact_id(app.add_artifact(session_id, str(sample)))
    hints = {
        "architecture": "arm",
        "endianness": "little",
        "bitness": 32,
        "platform": "baremetal",
        "base_address": "0x8000",
        "memory_map": [
            {
                "name": "flash",
                "file_offset": 0,
                "size": sample.stat().st_size,
                "virtual_address": "0x8000",
                "permissions": "r-x",
            }
        ],
    }

    triage = app.triage_artifact(session_id, artifact_id, hints=hints)
    assert triage["ok"] is True
    assert triage["result"]["file_type"]["kind"] == "firmware_image"
    assert triage["result"]["file_type"]["architecture"] == "arm"

    strings = app.list_artifact_strings(session_id, artifact_id, limit=10, hints=hints)
    assert strings["ok"] is True
    values = [item["value"] for item in strings["result"]["items"]]
    assert "boot" in values
    assert any(item["encoding"] == "utf-16le" for item in strings["result"]["items"])

    translated = app.translate_artifact_address(session_id, artifact_id, "file_offset", 0, hints=hints)
    assert translated["ok"] is True
    assert translated["result"]["matches"][0]["virtual_address"] == 0x8000


def test_zip_child_mapping_and_external_enrichment_hook(tmp_path: Path) -> None:
    archive_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("setup.exe", b"MZ-not-real")
        archive.writestr("docs/readme.txt", b"hello")

    app = ReversingMCPApp(workspace_root=tmp_path)
    session_id = _session_id(app.create_session("triage-zip"))
    artifact_id = _artifact_id(app.add_artifact(session_id, str(archive_path)))

    triage = app.triage_artifact(session_id, artifact_id)
    assert triage["ok"] is True
    assert triage["result"]["file_type"]["format"] == "ZIP"
    assert triage["result"]["taxonomy"][0]["category"] == "installer"

    children = app.list_artifact_children(session_id, artifact_id, limit=10)
    assert children["ok"] is True
    names = [item["name"] for item in children["result"]["items"]]
    assert "setup.exe" in names
    assert "docs/readme.txt" in names
    assert all("provenance" in item for item in children["result"]["items"])

    enrichment = app.lookup_external_enrichment(session_id, artifact_id, providers=["virustotal"], opt_in=True)
    assert enrichment["ok"] is True
    assert enrichment["result"]["status"] == "disabled"
    assert enrichment["result"]["opt_in_requested"] is True
