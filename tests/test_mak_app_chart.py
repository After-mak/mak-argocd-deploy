import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "mak-app"
FINOPS_CHART = ROOT / "charts" / "finops"


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
    canary = rollout["spec"]["strategy"]["canary"]
    assert canary["stableService"] == "mak-app-active"
    assert canary["canaryService"] == "mak-app-preview"
    assert "activeService" not in canary
    assert "previewService" not in canary
    assert canary["trafficRouting"]["plugins"]["argoproj-labs/gatewayAPI"] == {
        "httpRoute": "mak-app-route",
        "namespace": "frontend",
    }

    services = {
        item["metadata"]["name"]: item
        for item in docs
        if item.get("kind") == "Service"
    }
    assert services[canary["stableService"]]["spec"]["selector"] == rollout["spec"]["selector"]["matchLabels"]
    assert services[canary["canaryService"]]["spec"]["selector"] == rollout["spec"]["selector"]["matchLabels"]

    route = next(item for item in docs if item.get("kind") == "HTTPRoute")
    backend_refs = route["spec"]["rules"][0]["backendRefs"]
    assert [ref["name"] for ref in backend_refs] == [
        canary["stableService"], canary["canaryService"],
    ]
    assert [ref["weight"] for ref in backend_refs] == [100, 0]

    target_groups = [
        item for item in docs if item.get("kind") == "TargetGroupConfiguration"
    ]
    assert len(target_groups) == 2
    for target_group in target_groups:
        health_check = target_group["spec"]["defaultConfiguration"]["healthCheckConfig"]
        assert health_check["healthCheckPath"] == "/version"
        assert health_check["healthCheckPort"] == "8080"
        assert health_check["healthCheckProtocol"] == "HTTP"
        assert "healthCheck" not in target_group["spec"]["defaultConfiguration"]
        assert target_group["spec"]["targetReference"]["name"] in services

    job = next(item for item in docs if item.get("kind") == "Job")
    assert job["metadata"]["name"] == "bank-loadgen-pre-boa-pre-001"
    env = {
        item["name"]: item for item in job["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["RUN_ID"]["value"] == "BOA-PRE-001"
    assert env["PHASE"]["value"] == "pre"
    assert "K6_OUT" not in env
    assert env["TEST_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "bank-loadgen-credentials"
    args = job["spec"]["template"]["spec"]["containers"][0]["args"]
    assert "testid=BOA-PRE-001" in args
    assert "--summary-export=/results/summary.json" in args
    assert "json=/results/raw.json" in args
    assert "experimental-prometheus-rw" not in args


def test_remote_write_is_opt_in_and_keeps_run_id_tag_and_raw_results():
    docs = render(
        "components.backend=false",
        "loadgen.enabled=true",
        "loadgen.runId=BOA-SMOKE-RW-001",
        "loadgen.phase=smoke",
        "loadgen.profile=smoke",
        "loadgen.prometheusRemoteWriteEnabled=true",
        namespace="frontend",
    )
    job = next(item for item in docs if item.get("kind") == "Job")
    container = job["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item for item in container["env"]}
    assert "experimental-prometheus-rw" in container["args"]
    assert "testid=BOA-SMOKE-RW-001" in container["args"]
    assert env["K6_PROMETHEUS_RW_TREND_STATS"]["value"] == "p(50),p(95),p(99),avg,max"


def test_jwt_is_external_only_and_never_rendered_as_a_secret():
    frontend_docs = render(
        "components.backend=false",
        "secrets.existingSecret=bank-jwt-key-v2",
        namespace="frontend",
    )
    backend_docs = render(
        "components.frontend=false",
        "secrets.existingSecret=bank-jwt-key-v2",
        namespace="backend",
    )
    docs = frontend_docs + backend_docs

    assert not any(
        item.get("kind") == "Secret"
        and item.get("metadata", {}).get("name") in {"jwt-key", "bank-jwt-key-v2"}
        for item in docs
    )

    pod_specs = [
        item["spec"]["template"]["spec"]
        for item in docs
        if item.get("kind") in {"Deployment", "Rollout"}
    ]
    jwt_volumes = [
        volume
        for pod_spec in pod_specs
        for volume in pod_spec.get("volumes", [])
        if volume["name"] in {"keys", "publickey"}
    ]
    assert len(jwt_volumes) == 6
    assert {
        volume["secret"]["secretName"] for volume in jwt_volumes
    } == {"bank-jwt-key-v2"}


def test_jwt_external_secret_name_is_required():
    command = [
        "helm", "template", "mak-app", str(CHART), "--namespace", "frontend",
        "--set", "components.backend=false", "--set-string", "secrets.existingSecret=",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    assert result.returncode != 0
    assert "secrets.existingSecret is required" in result.stderr


def test_finops_workflow_has_explicit_bank_mapping_and_fails_unknown_targets():
    workflow = (ROOT / ".github" / "workflows" / "finops-apply.yaml").read_text()
    helper = (ROOT / ".github" / "scripts" / "update-resource-requests.sh").read_text()
    assert "container_name:" in workflow
    assert "frontend/mak-app-rollout/mak-container)" in workflow
    assert ".resources.${DEPLOYMENT_NAME}.worker.requests" in workflow
    assert "지원하지 않는 리소스 식별자" in workflow
    assert ".github/scripts/update-resource-requests.sh" in workflow
    assert '[[ ! -f "$VALUES_FILE" ]]' in helper
    assert '${RESOURCES_PATH}.cpu // \\"MISSING\\"' in helper
    assert '"$current_cpu" == "$CPU"' in helper
    assert '"$current_memory" == "$MEMORY"' in helper


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


def test_finops_rbac_grants_rollout_read_only_access():
    output = subprocess.run(
        ["helm", "template", "finops", str(FINOPS_CHART), "--namespace", "finops"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    expected_rule = """  - apiGroups: ["argoproj.io"]
    resources:
      - rollouts
      - rollouts/scale
    verbs: ["get", "list", "watch"]"""
    assert expected_rule in output
    assert 'verbs: ["create"' not in output
    assert 'verbs: ["delete"' not in output
