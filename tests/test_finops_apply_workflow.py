import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".github" / "scripts" / "update-resource-requests.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "finops-apply.yaml"


def _fake_yq(directory: Path) -> Path:
    executable = directory / "yq"
    executable.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "-r" ]]; then
  if [[ -f "$FAKE_YQ_LOG" ]]; then
    if [[ "$2" == *".cpu"* ]]; then
      printf '%s\\n' "$CPU"
    else
      printf '%s\\n' "$MEMORY"
    fi
    exit 0
  fi
  if [[ "$2" == *".cpu"* ]]; then
    printf '%s\\n' "$FAKE_CURRENT_CPU"
  else
    printf '%s\\n' "$FAKE_CURRENT_MEMORY"
  fi
  exit 0
fi
if [[ "$1" == "-i" ]]; then
  printf 'called\\n' > "$FAKE_YQ_LOG"
  exit 0
fi
exit 2
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _run_helper(values: Path, *, current_cpu: str, current_memory: str, cpu: str, memory: str):
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{values.parent}:{env['PATH']}",
            "VALUES_FILE": str(values),
            "RESOURCES_PATH": ".worker.resources.requests",
            "CPU": cpu,
            "MEMORY": memory,
            "FAKE_CURRENT_CPU": current_cpu,
            "FAKE_CURRENT_MEMORY": current_memory,
            "FAKE_YQ_LOG": str(values.parent / "yq-write.log"),
        }
    )
    return subprocess.run(
        ["bash", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_same_resource_values_skip_yq_write_and_preserve_file_bytes():
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        _fake_yq(directory)
        values = directory / "values.yaml"
        original = "query: >-\\n  max(metric)\\nresources: unchanged\\n"
        values.write_text(original, encoding="utf-8")

        result = _run_helper(
            values,
            current_cpu="425m",
            current_memory="142Mi",
            cpu="425m",
            memory="142Mi",
        )

        assert result.returncode == 0, result.stderr
        assert "변경 사항 없음" in result.stdout
        assert values.read_text(encoding="utf-8") == original
        assert not (directory / "yq-write.log").exists()


def test_different_resource_values_call_yq_write_once():
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        _fake_yq(directory)
        values = directory / "values.yaml"
        values.write_text("resources: unchanged\\n", encoding="utf-8")

        result = _run_helper(
            values,
            current_cpu="100m",
            current_memory="128Mi",
            cpu="425m",
            memory="142Mi",
        )

        assert result.returncode == 0, result.stderr
        assert "수정 완료" in result.stdout
        assert (directory / "yq-write.log").read_text(encoding="utf-8").splitlines() == ["called"]


def test_workflow_uses_idempotent_update_helper():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert ".github/scripts/update-resource-requests.sh" in workflow
    assert 'yq -i "${RESOURCES_PATH}.cpu' not in workflow
