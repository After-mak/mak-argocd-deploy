import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "mak-app"


def render(*sets: str, namespace: str) -> list[dict]:
    command = ["helm", "template", "mak-app", str(CHART), "--namespace", namespace]
    for value in sets:
        command += ["--set", value]
    output = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    return [item for item in yaml.safe_load_all(output) if item]


def deployments(documents: list[dict]) -> dict[str, dict]:
    return {
        item["metadata"]["name"]: item
        for item in documents
        if item.get("kind") == "Deployment"
    }


def test_all_bank_containers_render_explicit_requests_and_preserve_limits():
    docs = render("components.frontend=false", namespace="backend")
    result = deployments(docs)
    assert set(result) == {
        "userservice", "contacts", "balancereader", "ledgerwriter", "transactionhistory"
    }

    for workload, deployment in result.items():
        containers = {
            item["name"]: item for item in deployment["spec"]["template"]["spec"]["containers"]
        }
        app = containers[workload]["resources"]
        worker = containers["worker"]["resources"]
        assert app["requests"] == {"cpu": "200m", "memory": "256Mi"}
        assert app["limits"] == {}
        assert worker["requests"] == {"cpu": "250m", "memory": "256Mi"}
        assert worker["limits"] == {"cpu": "500m", "memory": "512Mi"}


def test_frontend_rollout_and_opt_in_load_job_render_expected_contract():
    disabled = render("components.backend=false", namespace="frontend")
    assert not any(item.get("kind") == "Job" for item in disabled)

    docs = render(
        "components.backend=false",
        "loadgen.enabled=true",
        "loadgen.runId=BOA-PRE-001",
        "loadgen.phase=pre",
        "loadgen.profile=long",
        "loadgen.cycles=18",
        namespace="frontend",
    )
    rollout = next(item for item in docs if item.get("kind") == "Rollout")
    frontend = rollout["spec"]["template"]["spec"]["containers"][0]
    assert frontend["name"] == "mak-container"
    assert frontend["resources"]["requests"] == {"cpu": "200m", "memory": "256Mi"}

    job = next(item for item in docs if item.get("kind") == "Job")
    assert job["metadata"]["name"] == "bank-loadgen-pre-boa-pre-001"
    env = {
        item["name"]: item for item in job["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["RUN_ID"]["value"] == "BOA-PRE-001"
    assert env["PHASE"]["value"] == "pre"
    assert "K6_OUT" not in env
    assert env["TEST_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "bank-loadgen-credentials"


def test_finops_workflow_has_explicit_bank_mapping_and_fails_unknown_targets():
    workflow = (ROOT / ".github" / "workflows" / "finops-apply.yaml").read_text()
    assert "container_name:" in workflow
    assert "frontend/mak-app-rollout/mak-container)" in workflow
    assert ".resources.${DEPLOYMENT_NAME}.worker.requests" in workflow
    assert "지원하지 않는 리소스 식별자" in workflow
    assert 'if [ ! -f "$VALUES_FILE" ]' in workflow
    assert '${RESOURCES_PATH}.cpu // \\"MISSING\\"' in workflow


def test_loadgen_rejects_phase_profile_mismatch():
    command = [
        "helm", "template", "mak-app", str(CHART), "--namespace", "frontend",
        "--set", "components.backend=false", "--set", "loadgen.enabled=true",
        "--set", "loadgen.runId=bad-pre", "--set", "loadgen.phase=pre",
        "--set", "loadgen.profile=smoke",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    assert result.returncode != 0
    assert "loadgen.profile must be long" in result.stderr


def pod_secret_volumes(workload: dict) -> dict[str, dict]:
    pod_spec = workload["spec"]["template"]["spec"]
    return {
        item["name"]: item["secret"]
        for item in pod_spec["volumes"]
        if "secret" in item
    }


def test_bank_workloads_reference_external_jwt_secret_without_rendering_key_material():
    secret_name = "bank-jwt-key-v2"
    backend_docs = render(
        "components.frontend=false",
        f"secrets.existingSecret={secret_name}",
        namespace="backend",
    )
    frontend_docs = render(
        "components.backend=false",
        f"secrets.existingSecret={secret_name}",
        namespace="frontend",
    )

    rendered_secrets = [
        item for item in backend_docs + frontend_docs if item.get("kind") == "Secret"
    ]
    assert all(item["metadata"]["name"] != secret_name for item in rendered_secrets)
    assert all(item["metadata"]["name"] != "jwt-key" for item in rendered_secrets)

    backend = deployments(backend_docs)
    for name, deployment in backend.items():
        jwt_volume = next(iter(pod_secret_volumes(deployment).values()))
        assert jwt_volume["secretName"] == secret_name
        keys = {item["key"] for item in jwt_volume["items"]}
        expected = (
            {"jwtRS256.key", "jwtRS256.key.pub"}
            if name == "userservice"
            else {"jwtRS256.key.pub"}
        )
        assert keys == expected

    rollout = next(item for item in frontend_docs if item.get("kind") == "Rollout")
    frontend_jwt_volume = pod_secret_volumes(rollout)["publickey"]
    assert frontend_jwt_volume["secretName"] == secret_name
    assert {item["key"] for item in frontend_jwt_volume["items"]} == {"jwtRS256.key.pub"}


def test_bank_chart_rejects_empty_external_jwt_secret_name():
    command = [
        "helm", "template", "mak-app", str(CHART), "--namespace", "backend",
        "--set", "components.frontend=false", "--set-string", "secrets.existingSecret=",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    assert result.returncode != 0
    assert "secrets.existingSecret is required" in result.stderr
