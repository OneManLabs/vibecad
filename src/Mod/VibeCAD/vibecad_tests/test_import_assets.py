# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

import VibeCADImportAssets as assets


PROJECT_ID = "import-contract-test"
ASSET_ID = "a" * 32


def _source(tmp_path: Path, content: bytes = b"ISO-10303-21;\nEND-ISO-10303-21;\n") -> Path:
    source = tmp_path / "source.step"
    source.write_bytes(content)
    return source


def _register(
    tmp_path: Path,
    *,
    source: Path | None = None,
    fault=None,
    asset_id: str = ASSET_ID,
) -> tuple[Path, dict]:
    root = tmp_path / "project"
    result = assets.register_import_asset(
        root,
        PROJECT_ID,
        source or _source(tmp_path),
        policy_check=lambda: None,
        permission_check=lambda permission: None,
        fault=fault,
        asset_id_factory=lambda: asset_id,
        now=lambda: "2026-07-22T12:00:00Z",
    )
    return root, result


def _contains_path_field(value) -> bool:
    if isinstance(value, dict):
        return any(
            key in {"path", "source_path", "relative_path", "internal_path"}
            or _contains_path_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_path_field(item) for item in value)
    return False


def test_registration_is_atomic_content_bound_and_provider_safe(tmp_path: Path) -> None:
    root, result = _register(tmp_path)

    assert result == {
        "schema": "vibecad-project-import-asset-v1",
        "version": 1,
        "asset_id": ASSET_ID,
        "stored_name": f"{ASSET_ID}.step",
        "format": "step",
        "size_bytes": 32,
        "sha256": assets._file_sha256(root / "import-assets" / f"{ASSET_ID}.step"),
        "created_at": "2026-07-22T12:00:00Z",
        "project_id": PROJECT_ID,
    }
    assert not _contains_path_field(result)
    manifest_path = root / "import-assets" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    content = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    assert manifest["manifest_sha256"] == assets._content_sha256(content)
    summary = assets.registered_import_assets(root, PROJECT_ID)
    assert summary["asset_count"] == 1
    assert summary["listed_asset_count"] == 1
    assert summary["assets_omitted"] == 0
    assert summary["assets"][0]["available"] is True
    assert summary["assets"][0]["availability"] == "verified"
    assert not _contains_path_field(summary)
    resolved = assets.resolve_import_asset(root, PROJECT_ID, ASSET_ID)
    assert resolved["path"] == root / "import-assets" / f"{ASSET_ID}.step"


def test_policy_and_permission_run_before_source_inspection(tmp_path: Path) -> None:
    events = []

    def deny(_permission):
        events.append("permission")
        raise PermissionError("blocked")

    with pytest.raises(PermissionError, match="blocked"):
        assets.register_import_asset(
            tmp_path / "project",
            PROJECT_ID,
            tmp_path / "missing.step",
            policy_check=lambda: events.append("policy"),
            permission_check=deny,
        )

    assert events == ["policy", "permission"]
    assert not (tmp_path / "project").exists()


@pytest.mark.parametrize(
    "value",
    ["/private/tmp/file.step", "../file.step", "a" * 31, "g" * 32, ""],
)
def test_provider_asset_input_rejects_paths_and_invalid_ids(
    tmp_path: Path, value: str
) -> None:
    root, _result = _register(tmp_path)
    with pytest.raises(ValueError, match="asset id"):
        assets.resolve_import_asset(root, PROJECT_ID, value)


def test_source_symlink_is_rejected(tmp_path: Path) -> None:
    original = _source(tmp_path)
    link = tmp_path / "link.step"
    link.symlink_to(original)
    with pytest.raises(ValueError, match="symbolic link"):
        _register(tmp_path, source=link)
    assert not (tmp_path / "project").exists()


@pytest.mark.parametrize("suffix", [".stl", ".obj", ".txt", ""])
def test_unsupported_import_formats_are_rejected(tmp_path: Path, suffix: str) -> None:
    source = tmp_path / f"source{suffix}"
    source.write_bytes(b"content")
    with pytest.raises(ValueError, match=".step or .stp"):
        _register(tmp_path, source=source)


def test_empty_and_oversized_import_assets_are_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    empty = _source(tmp_path, b"")
    with pytest.raises(ValueError, match="must contain"):
        _register(tmp_path, source=empty)
    monkeypatch.setattr(assets, "MAX_IMPORT_ASSET_BYTES", 4)
    large = _source(tmp_path, b"12345")
    with pytest.raises(ValueError, match="must contain"):
        _register(tmp_path, source=large)


def test_duplicate_promotion_does_not_overwrite_registered_bytes(tmp_path: Path) -> None:
    root, first = _register(tmp_path)
    target = root / "import-assets" / first["stored_name"]
    original = target.read_bytes()
    replacement = tmp_path / "replacement.step"
    replacement.write_bytes(b"different STEP bytes")

    with pytest.raises(RuntimeError, match="already registered|already exists"):
        _register(tmp_path, source=replacement)

    assert target.read_bytes() == original
    assert assets.registered_import_assets(root, PROJECT_ID)["asset_count"] == 1


def test_duplicate_content_is_rejected_for_a_new_asset_id(tmp_path: Path) -> None:
    root, first = _register(tmp_path)
    duplicate = tmp_path / "duplicate.step"
    duplicate.write_bytes((root / "import-assets" / first["stored_name"]).read_bytes())

    with pytest.raises(ValueError, match="content is already registered"):
        _register(tmp_path, source=duplicate, asset_id="b" * 32)

    summary = assets.registered_import_assets(root, PROJECT_ID)
    assert summary["asset_count"] == 1
    assert not (root / "import-assets" / f"{'b' * 32}.step").exists()


def test_missing_tampered_and_symlinked_assets_fail_closed(tmp_path: Path) -> None:
    root, result = _register(tmp_path)
    target = root / "import-assets" / result["stored_name"]
    target.unlink()
    with pytest.raises(ValueError, match="missing or unsafe"):
        assets.resolve_import_asset(root, PROJECT_ID, ASSET_ID)

    second_source = tmp_path / "second.step"
    second_source.write_bytes(b"second registered STEP content")
    root, result = _register(
        tmp_path, source=second_source, asset_id="b" * 32
    )
    target = root / "import-assets" / result["stored_name"]
    original_size = target.stat().st_size
    target.write_bytes(b"x" * original_size)
    with pytest.raises(ValueError, match="changed content"):
        assets.resolve_import_asset(root, PROJECT_ID, "b" * 32)

    third_source = tmp_path / "third.step"
    third_source.write_bytes(b"third registered STEP content")
    root, result = _register(
        tmp_path, source=third_source, asset_id="c" * 32
    )
    target = root / "import-assets" / result["stored_name"]
    safe_copy = tmp_path / "safe-copy.step"
    safe_copy.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(safe_copy)
    with pytest.raises(ValueError, match="missing or unsafe"):
        assets.resolve_import_asset(root, PROJECT_ID, "c" * 32)


@pytest.mark.parametrize("root_value", ["relative/project", "/"])
def test_unsafe_project_roots_are_rejected(tmp_path: Path, root_value: str) -> None:
    with pytest.raises(ValueError, match="root"):
        assets.register_import_asset(
            root_value,
            PROJECT_ID,
            _source(tmp_path),
            policy_check=lambda: None,
            permission_check=lambda _permission: None,
        )


def test_symlinked_import_store_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "import-assets").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="store is unsafe"):
        assets.register_import_asset(
            root,
            PROJECT_ID,
            _source(tmp_path),
            policy_check=lambda: None,
            permission_check=lambda _permission: None,
        )


@pytest.mark.parametrize(
    "stage",
    [
        "after_authorization",
        "before_source_read",
        "after_copy",
        "before_asset_promotion",
        "after_asset_promotion",
        "before_manifest_write",
        "after_manifest_temp_write",
        "before_manifest_promotion",
        "after_manifest_promotion",
        "after_manifest_write",
        "after_journal_deletion",
        "before_final_directory_sync",
        "after_final_directory_sync",
    ],
)
def test_faults_at_each_registration_boundary_leave_no_promoted_asset(
    tmp_path: Path, stage: str
) -> None:
    def inject(observed):
        if observed == stage:
            raise RuntimeError(f"fault:{stage}")

    with pytest.raises(RuntimeError, match=f"fault:{stage}"):
        _register(tmp_path, fault=inject)

    root = tmp_path / "project"
    if root.exists():
        summary = assets.registered_import_assets(root, PROJECT_ID)
        assert summary["asset_count"] == 0
        assert not list((root / "import-assets").glob("*.step"))


def test_atomic_manifest_write_failure_removes_promoted_asset(
    tmp_path: Path, monkeypatch
) -> None:
    original = assets._atomic_write_manifest
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("manifest write fault: /private/secret/model.step")
        return original(*args, **kwargs)

    monkeypatch.setattr(assets, "_atomic_write_manifest", fail_once)
    with pytest.raises(RuntimeError) as caught:
        _register(tmp_path)
    assert "/private/secret" not in str(caught.value)
    store = tmp_path / "project" / "import-assets"
    assert not list(store.glob("*.step"))
    assert not list(store.glob("*.tmp"))
    assert not (store / assets.IMPORT_ASSET_JOURNAL).exists()


def test_post_manifest_fault_restores_prior_state_in_same_call(tmp_path: Path) -> None:
    root, first = _register(tmp_path)
    source = tmp_path / "second.step"
    source.write_bytes(b"different registered STEP content")

    def inject(stage: str) -> None:
        if stage == "after_manifest_write":
            raise RuntimeError(f"fault:{stage}")

    with pytest.raises(RuntimeError, match="fault:after_manifest_write"):
        _register(tmp_path, source=source, asset_id="b" * 32, fault=inject)

    # Read the bytes and manifest directly.  Do not call a recovery API first.
    manifest = json.loads(
        (root / "import-assets" / assets.IMPORT_ASSET_MANIFEST).read_text(
            encoding="utf-8"
        )
    )
    assert [item["asset_id"] for item in manifest["assets"]] == [ASSET_ID]
    assert not (root / "import-assets" / f"{'b' * 32}.step").exists()
    assert not (root / "import-assets" / assets.IMPORT_ASSET_JOURNAL).exists()
    summary = assets.registered_import_assets(root, PROJECT_ID)
    assert summary["asset_count"] == 1
    registered_names = {item["stored_name"] for item in summary["assets"]}
    stored_names = {path.name for path in (root / "import-assets").glob("*.step")}
    assert registered_names == stored_names
    assert first["stored_name"] in stored_names


def test_manifest_tampering_and_project_mismatch_fail_closed(tmp_path: Path) -> None:
    root, _result = _register(tmp_path)
    manifest_path = root / "import-assets" / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["assets"][0]["size_bytes"] += 1
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RuntimeError, match="content hash"):
        assets.registered_import_assets(root, PROJECT_ID)

    with pytest.raises(RuntimeError, match="another project|content hash"):
        assets.registered_import_assets(root, "different-project")


def _subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    module_root = str(Path(assets.__file__).resolve().parent)
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        module_root if not existing else module_root + os.pathsep + existing
    )
    return environment


_REGISTER_CHILD = r"""
import os
import sys
from VibeCADImportAssets import register_import_asset

root, project_id, source, asset_id, crash_stage = sys.argv[1:]

def fault(stage):
    if crash_stage and stage == crash_stage:
        os._exit(77)

register_import_asset(
    root,
    project_id,
    source,
    policy_check=lambda: None,
    permission_check=lambda _permission: None,
    fault=fault,
    asset_id_factory=lambda: asset_id,
    now=lambda: "2026-07-22T12:00:00Z",
)
"""


def _run_registration_child(
    root: Path,
    source: Path,
    asset_id: str,
    *,
    crash_stage: str = "",
) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _REGISTER_CHILD,
            str(root),
            PROJECT_ID,
            str(source),
            asset_id,
            crash_stage,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_subprocess_environment(),
    )


def test_provider_listing_hashes_bytes_and_never_uses_size_only(
    tmp_path: Path,
) -> None:
    root, result = _register(tmp_path)
    target = root / assets.IMPORT_ASSET_DIRECTORY / result["stored_name"]
    target.write_bytes(b"x" * result["size_bytes"])

    summary = assets.registered_import_assets(root, PROJECT_ID)

    assert summary["assets"][0]["available"] is False
    assert summary["assets"][0]["availability"] == "changed"

    target.unlink()
    missing = assets.registered_import_assets(root, PROJECT_ID)
    assert missing["assets"][0]["available"] is False
    assert missing["assets"][0]["availability"] == "missing"


def test_provider_listing_verifies_only_the_bounded_newest_subset(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "project"
    for index in range(15):
        source = tmp_path / f"source-{index}.step"
        source.write_bytes(f"STEP asset {index}".encode("ascii"))
        assets.register_import_asset(
            root,
            PROJECT_ID,
            source,
            policy_check=lambda: None,
            permission_check=lambda _permission: None,
            asset_id_factory=lambda index=index: f"{index + 1:032x}",
            now=lambda: "2026-07-22T12:00:00Z",
        )
    checked: list[str] = []
    original = assets._asset_availability

    def observe(store, entry, **kwargs):
        checked.append(entry["asset_id"])
        return original(store, entry, **kwargs)

    monkeypatch.setattr(assets, "_asset_availability", observe)
    summary = assets.registered_import_assets(root, PROJECT_ID, limit=3)

    assert summary["asset_count"] == 15
    assert summary["listed_asset_count"] == 3
    assert summary["assets_omitted"] == 12
    assert checked == [f"{index:032x}" for index in (13, 14, 15)]
    assert all(item["availability"] == "verified" for item in summary["assets"])


def test_provider_listing_reuses_only_a_stable_identity_digest(
    tmp_path: Path, monkeypatch
) -> None:
    root, result = _register(tmp_path)
    calls = []
    original = assets._hash_verified_descriptor

    def observe(*args, **kwargs):
        calls.append(str(args[1]))
        return original(*args, **kwargs)

    monkeypatch.setattr(assets, "_hash_verified_descriptor", observe)
    progress = []
    first = assets.registered_import_assets(
        root, PROJECT_ID, progress_callback=progress.append
    )
    second = assets.registered_import_assets(
        root, PROJECT_ID, progress_callback=progress.append
    )

    assert calls == [result["stored_name"]]
    assert first["assets"][0]["availability"] == "verified"
    assert second["assets"][0]["availability"] == "verified"
    assert any(item["cached"] is False for item in progress)
    assert any(item["cached"] is True for item in progress)
    assert all("path" not in item for item in progress)


def test_provider_listing_can_cancel_during_asset_authentication(
    tmp_path: Path,
) -> None:
    root, _result = _register(tmp_path)
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    with pytest.raises(assets.ImportAssetScanCancelled, match="cancelled"):
        assets.registered_import_assets(
            root,
            PROJECT_ID,
            cancellation_check=cancelled,
        )

    assert checks >= 2


def test_secure_private_copy_is_exact_exclusive_and_path_free(tmp_path: Path) -> None:
    root, result = _register(tmp_path)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    destination = private / "registered.step"

    copied = assets.copy_registered_import_asset(
        root, PROJECT_ID, ASSET_ID, destination
    )

    assert destination.read_bytes() == (
        root / assets.IMPORT_ASSET_DIRECTORY / result["stored_name"]
    ).read_bytes()
    assert copied["sha256"] == result["sha256"]
    assert not _contains_path_field(copied)
    with pytest.raises(RuntimeError, match="store operation failed"):
        assets.copy_registered_import_asset(root, PROJECT_ID, ASSET_ID, destination)


def test_platform_without_no_follow_identity_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(assets, "_descriptor_security_supported", lambda: False)
    with pytest.raises(RuntimeError, match="cannot prove safe no-follow"):
        _register(tmp_path)
    assert not (tmp_path / "project").exists()


def test_filesystem_error_text_does_not_cross_the_public_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    secret = "/private/organization/secret-design/import-assets"

    def fail(_root):
        raise OSError(13, "Permission denied", secret)

    monkeypatch.setattr(assets, "_open_secure_store", fail)
    with pytest.raises(RuntimeError) as caught:
        assets.registered_import_assets(tmp_path / "project", PROJECT_ID)
    assert str(caught.value) == "The STEP import asset store operation failed."
    assert secret not in str(caught.value)


def test_lock_contention_has_a_bounded_stable_failure(tmp_path: Path) -> None:
    root = tmp_path / "project"
    with assets._locked_store(root, PROJECT_ID):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(
                assets.registered_import_assets,
                root,
                PROJECT_ID,
                lock_timeout_seconds=0.05,
            )
            with pytest.raises(RuntimeError, match="store is busy"):
                result.result(timeout=1.0)


@pytest.mark.parametrize("timeout", [-1.0, float("inf"), float("nan"), True])
def test_lock_timeout_must_be_finite_and_nonnegative(
    tmp_path: Path, timeout: float
) -> None:
    with pytest.raises(ValueError, match="finite and nonnegative"):
        assets.registered_import_assets(
            tmp_path / "project",
            PROJECT_ID,
            lock_timeout_seconds=timeout,
        )


def test_process_lock_contention_has_a_bounded_stable_failure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    child_code = r"""
import sys
from VibeCADImportAssets import registered_import_assets
try:
    registered_import_assets(sys.argv[1], sys.argv[2], lock_timeout_seconds=0.1)
except RuntimeError as exc:
    if str(exc) == "The project import asset store is busy.":
        raise SystemExit(23)
    print(str(exc), file=sys.stderr)
    raise SystemExit(24)
raise SystemExit(25)
"""
    with assets._locked_store(root, PROJECT_ID):
        child = subprocess.run(
            [sys.executable, "-c", child_code, str(root), PROJECT_ID],
            capture_output=True,
            text=True,
            env=_subprocess_environment(),
            timeout=2,
            check=False,
        )
    assert child.returncode == 23, (child.stdout, child.stderr)


def test_concurrent_thread_registrations_keep_every_manifest_entry(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    barrier = threading.Barrier(6)

    def register(index: int) -> dict:
        source = tmp_path / f"thread-{index}.step"
        source.write_bytes(f"thread STEP {index}".encode("ascii"))
        barrier.wait(timeout=2.0)
        return assets.register_import_asset(
            root,
            PROJECT_ID,
            source,
            policy_check=lambda: None,
            permission_check=lambda _permission: None,
            asset_id_factory=lambda: f"{index + 1:032x}",
            now=lambda: "2026-07-22T12:00:00Z",
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(register, range(6)))

    summary = assets.registered_import_assets(root, PROJECT_ID, limit=12)
    assert summary["asset_count"] == 6
    assert {item["asset_id"] for item in summary["assets"]} == {
        item["asset_id"] for item in results
    }
    assert not list((root / assets.IMPORT_ASSET_DIRECTORY).glob("*.tmp"))


def test_concurrent_process_registrations_are_serialized(tmp_path: Path) -> None:
    root = tmp_path / "project"
    first_source = tmp_path / "process-one.step"
    second_source = tmp_path / "process-two.step"
    first_source.write_bytes(b"process STEP one")
    second_source.write_bytes(b"process STEP two")
    children = [
        _run_registration_child(root, first_source, "1" * 32),
        _run_registration_child(root, second_source, "2" * 32),
    ]
    results = [child.communicate(timeout=10) for child in children]
    assert [child.returncode for child in children] == [0, 0], results

    summary = assets.registered_import_assets(root, PROJECT_ID, limit=12)
    assert summary["asset_count"] == 2
    assert {item["asset_id"] for item in summary["assets"]} == {
        "1" * 32,
        "2" * 32,
    }


@pytest.mark.parametrize(
    "stage",
    [
        "after_asset_promotion",
        "after_manifest_promotion",
        "after_journal_deletion",
        "before_final_directory_sync",
        "after_final_directory_sync",
    ],
)
def test_failed_call_restores_prior_bytes_and_manifest_before_return(
    tmp_path: Path, stage: str
) -> None:
    root, first = _register(tmp_path)
    prior_manifest = (root / assets.IMPORT_ASSET_DIRECTORY / assets.IMPORT_ASSET_MANIFEST).read_bytes()
    source = tmp_path / "next.step"
    source.write_bytes(b"next STEP content")

    def inject(observed: str) -> None:
        if observed == stage:
            raise RuntimeError(f"fault:{stage}")

    with pytest.raises(RuntimeError, match=f"fault:{stage}"):
        _register(tmp_path, source=source, asset_id="b" * 32, fault=inject)

    store = root / assets.IMPORT_ASSET_DIRECTORY
    assert (store / assets.IMPORT_ASSET_MANIFEST).read_bytes() == prior_manifest
    assert (store / first["stored_name"]).is_file()
    assert not (store / f"{'b' * 32}.step").exists()
    assert not (store / assets.IMPORT_ASSET_JOURNAL).exists()
    assert not list(store.glob("*.tmp"))


def test_hard_process_exit_after_asset_promotion_recovers_prior_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    source = tmp_path / "crash.step"
    source.write_bytes(b"crash recovery STEP")
    child = _run_registration_child(
        root, source, "3" * 32, crash_stage="after_asset_promotion"
    )
    output = child.communicate(timeout=10)
    assert child.returncode == 77, output
    store = root / assets.IMPORT_ASSET_DIRECTORY
    assert (store / assets.IMPORT_ASSET_JOURNAL).is_file()
    assert (store / f"{'3' * 32}.step").is_file()
    journal = json.loads(
        (store / assets.IMPORT_ASSET_JOURNAL).read_text(encoding="utf-8")
    )
    journal_content = {
        key: value for key, value in journal.items() if key != "journal_sha256"
    }
    assert journal["schema"] == assets.IMPORT_ASSET_JOURNAL_SCHEMA
    assert journal["version"] == assets.IMPORT_ASSET_JOURNAL_VERSION
    assert journal["journal_sha256"] == assets._content_sha256(journal_content)

    summary = assets.registered_import_assets(root, PROJECT_ID)

    assert summary["asset_count"] == 0
    assert not (store / f"{'3' * 32}.step").exists()
    assert not (store / assets.IMPORT_ASSET_JOURNAL).exists()
    assert not list(store.glob("*.tmp"))
    # The persistent lock file is safe after the owner process exits.
    next_source = tmp_path / "next-after-crash.step"
    next_source.write_bytes(b"next content after crash")
    result = assets.register_import_asset(
        root,
        PROJECT_ID,
        next_source,
        policy_check=lambda: None,
        permission_check=lambda _permission: None,
        asset_id_factory=lambda: "4" * 32,
    )
    assert result["asset_id"] == "4" * 32


def test_recovery_rejects_manifest_with_unrelated_authenticated_change(
    tmp_path: Path,
) -> None:
    root, first = _register(tmp_path)
    source = tmp_path / "crash-second.step"
    source.write_bytes(b"second crash STEP")
    child = _run_registration_child(
        root, source, "5" * 32, crash_stage="after_asset_promotion"
    )
    output = child.communicate(timeout=10)
    assert child.returncode == 77, output
    store = root / assets.IMPORT_ASSET_DIRECTORY
    journal = json.loads(
        (store / assets.IMPORT_ASSET_JOURNAL).read_text(encoding="utf-8")
    )
    extra = {
        **journal["entry"],
        "asset_id": "6" * 32,
        "stored_name": f"{'6' * 32}.step",
        "sha256": "0" * 64,
        "size_bytes": 1,
    }
    forged_content = {
        "schema": assets.IMPORT_ASSET_SCHEMA,
        "version": assets.IMPORT_ASSET_VERSION,
        "project_id": PROJECT_ID,
        "updated_at": journal["updated_at"],
        "assets": [*journal["prior_manifest"]["assets"], journal["entry"], extra],
    }
    (store / assets.IMPORT_ASSET_MANIFEST).write_text(
        json.dumps(assets._manifest_payload(forged_content)), encoding="ascii"
    )

    summary = assets.registered_import_assets(root, PROJECT_ID)

    assert summary["asset_count"] == 1
    assert summary["assets"][0]["asset_id"] == first["asset_id"]
    assert not (store / f"{'5' * 32}.step").exists()
    assert not (store / assets.IMPORT_ASSET_JOURNAL).exists()


def test_source_file_path_swap_during_copy_fails_closed(tmp_path: Path) -> None:
    input_directory = tmp_path / "input"
    input_directory.mkdir()
    source = input_directory / "source.step"
    source.write_bytes(b"original source identity")

    def swap(stage: str) -> None:
        if stage == "after_source_copy":
            source.rename(input_directory / "original.step")
            source.write_bytes(b"replacement source bytes")

    with pytest.raises(RuntimeError, match="changed while it was copied"):
        _register(tmp_path, source=source, fault=swap)
    store = tmp_path / "project" / assets.IMPORT_ASSET_DIRECTORY
    assert not list(store.glob("*.step"))
    assert not list(store.glob("*.tmp"))


def test_source_directory_path_swap_during_copy_fails_closed(tmp_path: Path) -> None:
    input_directory = tmp_path / "input"
    input_directory.mkdir()
    source = input_directory / "source.step"
    source.write_bytes(b"original source directory identity")

    def swap(stage: str) -> None:
        if stage == "after_source_copy":
            input_directory.rename(tmp_path / "moved-input")
            input_directory.mkdir()
            (input_directory / "source.step").write_bytes(b"replacement")

    with pytest.raises(RuntimeError, match="changed while it was copied"):
        _register(tmp_path, source=source, fault=swap)
    store = tmp_path / "project" / assets.IMPORT_ASSET_DIRECTORY
    assert not list(store.glob("*.step"))
    assert not list(store.glob("*.tmp"))


def test_store_directory_path_swap_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = _source(tmp_path)

    def swap(stage: str) -> None:
        if stage == "after_copy":
            store = root / assets.IMPORT_ASSET_DIRECTORY
            store.rename(root / "moved-import-assets")
            store.mkdir(mode=0o700)

    with pytest.raises(RuntimeError, match="identity changed|recovery did not complete"):
        assets.register_import_asset(
            root,
            PROJECT_ID,
            source,
            policy_check=lambda: None,
            permission_check=lambda _permission: None,
            fault=swap,
            asset_id_factory=lambda: ASSET_ID,
        )
    canonical = root / assets.IMPORT_ASSET_DIRECTORY
    assert not list(canonical.glob("*.step"))
    assert not (canonical / assets.IMPORT_ASSET_MANIFEST).exists()


def test_pre_journal_write_failure_leaves_no_private_orphan(
    tmp_path: Path, monkeypatch
) -> None:
    def fail(_store, _journal):
        raise RuntimeError("journal write fault")

    monkeypatch.setattr(assets, "_write_journal", fail)
    with pytest.raises(RuntimeError, match="journal write fault"):
        _register(tmp_path)
    store = tmp_path / "project" / assets.IMPORT_ASSET_DIRECTORY
    assert not list(store.glob("*.step"))
    assert not list(store.glob("*.tmp"))
    assert not (store / assets.IMPORT_ASSET_JOURNAL).exists()
