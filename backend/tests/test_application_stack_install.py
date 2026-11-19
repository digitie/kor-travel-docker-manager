"""정상 installer의 실제 pip 검사와 source exact 버전 소비를 작은 로컬 metadata로 검증한다."""
from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pip

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts/install-ktdm-trusted-release"


def source_pins():
    declared = tomllib.loads((ROOT / "backend/pyproject.toml").read_text())["tool"]["poetry"]["dependencies"]
    return {name: spec[2:] for name, spec in declared.items()
            if isinstance(spec, str) and spec.startswith("==")}


def runtime_block():
    text = INSTALLER.read_text()
    return text[text.index("# >>> backend-runtime"):text.index("# <<< backend-runtime")]


def fixture_release(tmp_path, *, stale=False, require_exact=True, missing_core=False):
    release = tmp_path / "release"
    backend = release / "backend"
    backend.mkdir(parents=True)
    (backend / "pyproject.toml").write_bytes((ROOT / "backend/pyproject.toml").read_bytes())
    (backend / "constraints.txt").write_bytes((ROOT / "backend/constraints.txt").read_bytes())
    venv = backend / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    python = venv / "bin/python"
    site = Path(subprocess.check_output(
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip())
    # pip 코드만 공유한다. site-packages/distribution은 이 시험의 작은 독립 venv다.
    (site / "pip").symlink_to(Path(pip.__file__).parent, target_is_directory=True)
    pins = {**source_pins(), "pip": "26.3"}
    for name, version in pins.items():
        if missing_core and name == "pydantic-core":
            continue
        installed = "0.110.0" if stale and name == "fastapi" else version
        dist = site / (name.replace("-", "_") + "-" + installed + ".dist-info")
        dist.mkdir()
        (dist / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {installed}\n")
    dist = site / "fixture_backend-1.0.dist-info"
    dist.mkdir()
    requires = "".join(f"Requires-Dist: {name}=={version}\n" for name, version in pins.items()) if require_exact else ""
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: fixture-backend\nVersion: 1.0\n" + requires)
    return release


def run_check(release):
    script = ('set -euo pipefail\nREL="$1"\n' + runtime_block()
              + '\nverify_backend_runtime\ntouch "$REL/FLIPPED"\n')
    return subprocess.run(["bash", "-c", script, "test", str(release)],
                          capture_output=True, text=True, check=False)


def test_real_pip_and_exact_runtime_positive(tmp_path):
    release = fixture_release(tmp_path)
    result = run_check(release)
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert records[-1]["versions"] == {**source_pins(), "pip": "26.3"}
    assert (release / "FLIPPED").exists()


def test_real_pip_refuses_no_deps_graph_mismatch_before_flip(tmp_path):
    release = fixture_release(tmp_path, stale=True)
    result = run_check(release)
    assert result.returncode != 0
    assert "fastapi" in result.stdout
    assert not (release / "FLIPPED").exists()



def test_pip_check_rejects_broken_unselected_dependency_even_when_all_pins_match(tmp_path):
    release = fixture_release(tmp_path)
    python = release / "backend/.venv/bin/python"
    site = Path(subprocess.check_output(
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip())
    dist = site / "fixture_unselected-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: fixture-unselected\nVersion: 1.0\nRequires-Dist: fixture-missing-dependency==1.0\n")
    result = run_check(release)
    assert result.returncode != 0
    assert "fixture-missing-dependency" in result.stdout
    assert not (release / "FLIPPED").exists()

def test_valid_but_old_environment_is_rejected_by_source_readback(tmp_path):
    release = fixture_release(tmp_path, stale=True, require_exact=False)
    result = run_check(release)
    assert result.returncode != 0
    assert "No broken requirements found" in result.stdout
    assert "backend exact dependency mismatch: fastapi" in result.stderr
    assert not (release / "FLIPPED").exists()


def test_missing_paired_core_cannot_pass_readback(tmp_path):
    release = fixture_release(tmp_path, missing_core=True, require_exact=False)
    result = run_check(release)
    assert result.returncode != 0
    assert "backend exact dependency is missing: pydantic-core" in result.stderr
    assert not (release / "FLIPPED").exists()


def test_new_and_cached_release_both_check_before_activation():
    text = INSTALLER.read_text()
    prefix = 'if [[ "$(cat "' + chr(36) + '{REL}/.ktdm-source-revision"'
    branch = text[text.index(prefix):text.index("# UI는 docker-compose.yml을 제자리에서 고친다.")]
    fresh, cached = branch.split("\nelse\n", 1)
    marker = '> "' + chr(36) + '{REL}/.ktdm-source-revision"'
    assert fresh.index("verify_backend_runtime") < fresh.index(marker)
    assert "verify_backend_runtime" in cached
    assert text.index("# <<< backend-runtime") < text.index("# UI는 docker-compose.yml을 제자리에서 고친다.")
    assert "--no-deps" not in text


def test_installed_environment_matches_every_exact_backend_declaration():
    from importlib.metadata import version
    pins = source_pins()
    assert pins
    assert {name: version(name) for name in pins} == pins
    deps = tomllib.loads((ROOT / "backend/pyproject.toml").read_text())["tool"]["poetry"]["dependencies"]
    assert not {"asyncpg", "psycopg", "psycopg-binary", "kor-travel-common"} & set(deps)


def test_valid_graph_old_transitive_is_rejected_before_activation(tmp_path):
    release = fixture_release(tmp_path)
    python = release / "backend/.venv/bin/python"
    site = Path(subprocess.check_output(
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        text=True).strip())
    dist = site / "anyio-4.15.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: anyio\nVersion: 4.15.1\n")
    result = run_check(release)
    assert result.returncode != 0
    assert "No broken requirements found" in result.stdout
    assert "backend exact dependency mismatch: anyio" in result.stderr
    assert not (release / "FLIPPED").exists()


def test_constraint_cannot_override_exact_source_before_activation(tmp_path):
    release = fixture_release(tmp_path)
    path = release / "backend/constraints.txt"
    text = path.read_text()
    assert "fastapi==0.165.0" in text
    path.write_text(text.replace("fastapi==0.165.0", "fastapi==0.164.0"))
    result = run_check(release)
    assert result.returncode != 0
    assert "backend source and constraint differ: fastapi" in result.stderr
    assert not (release / "FLIPPED").exists()
