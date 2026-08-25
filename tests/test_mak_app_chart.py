import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "mak-app"
CHART_VALUES = yaml.safe_load((CHART / "values.yaml").read_text())


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
        expected = CHART_VALUES["resources"][workload]
        assert app == expected["app"]
        assert worker == expected["worker"]


def test_frontend_rollout_and_opt_in_load_job_render_expected_contract():
    disabled = render("components.backend=false", "loadgen.enabled=false", namespace="frontend")
    assert not any(item.get("kind") == "Job" for item in disabled)
    assert not any(item.get("kind") == "PersistentVolumeClaim" for item in disabled)

    docs = render(
        "components.backend=false",
        "loadgen.enabled=true",
        "loadgen.runId=BOA-PRE-001",
        "loadgen.phase=pre",
        "loadgen.profile=long",
        "loadgen.cycles=18",
        "waitingRoom.enabled=true",
        namespace="frontend",
    )
    rollout = next(item for item in docs if item.get("kind") == "Rollout")
    frontend = rollout["spec"]["template"]["spec"]["containers"][0]
    assert frontend["name"] == "mak-container"
    assert frontend["resources"] == CHART_VALUES["resources"]["frontend"]
    assert frontend["startupProbe"]["tcpSocket"] == {"port": 8080}
    assert frontend["startupProbe"]["failureThreshold"] == 30
    assert frontend["readinessProbe"]["tcpSocket"] == {"port": 8080}
    assert frontend["readinessProbe"]["periodSeconds"] == 2
    frontend_env = {item["name"]: item for item in frontend["env"]}
    assert frontend_env["ENABLE_WAITING_ROOM"]["value"] == "true"
    scaled_object = next(item for item in docs if item.get("kind") == "ScaledObject")
    assert {trigger["name"] for trigger in scaled_object["spec"]["triggers"]} == {
        "chronos-predictive", "frontend-http-cpu", "frontend-http-network", "waiting-queue"
    }

    pvc = next(item for item in docs if item.get("kind") == "PersistentVolumeClaim")
    assert pvc["metadata"]["name"] == "bank-loadgen-results"
    assert pvc["metadata"]["annotations"]["argocd.argoproj.io/sync-options"] == "Prune=false"
    assert pvc["metadata"]["annotations"]["argocd.argoproj.io/compare-options"] == "IgnoreExtraneous"
    assert pvc["spec"]["storageClassName"] == "ebs-gp3"
    assert pvc["spec"]["resources"]["requests"]["storage"] == "20Gi"
    script_config_map = next(
        item for item in docs
        if item.get("kind") == "ConfigMap" and item["metadata"]["name"].endswith("-script")
    )
    script = script_config_map["data"]["bank-of-anthos-long-run.js"]
    assert "summary-${SAFE_RUN_ID}.json" in script
    assert "export function setup()" in script
    assert "installSharedToken(data)" in script
    assert "scenario_schedule: SCENARIO_SCHEDULE" in script

    job = next(item for item in docs if item.get("kind") == "Job")
    assert job["metadata"]["name"] == "bank-loadgen-pre-boa-pre-001"
    assert job["spec"]["template"]["metadata"]["annotations"] == {
        "karpenter.sh/do-not-disrupt": "true"
    }
    pod_spec = job["spec"]["template"]["spec"]
    assert pod_spec["securityContext"]["fsGroup"] == 12345
    container = pod_spec["containers"][0]
    assert "--summary-export=/results/k6-summary-boa-pre-001.json" in container["args"]
    assert "json=/results/raw-boa-pre-001.json" in container["args"]
    results_volume = next(item for item in pod_spec["volumes"] if item["name"] == "results")
    assert results_volume["persistentVolumeClaim"]["claimName"] == "bank-loadgen-results"
    env = {item["name"]: item for item in container["env"]}
    assert env["RUN_ID"]["value"] == "BOA-PRE-001"
    assert env["PHASE"]["value"] == "pre"
    assert env["REQUEST_TIMEOUT"]["value"] == "10s"
    assert env["AUTH_MODE"]["value"] == "shared"
    assert "K6_OUT" not in env
    assert env["TEST_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "bank-loadgen-credentials"


def test_frontend_waiting_room_can_be_disabled_for_krr_experiments():
    docs = render(
        "components.backend=false",
        "waitingRoom.enabled=false",
        namespace="frontend",
    )
    rollout = next(item for item in docs if item.get("kind") == "Rollout")
    frontend = rollout["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item for item in frontend["env"]}
    assert env["ENABLE_WAITING_ROOM"]["value"] == "false"
    scaled_object = next(item for item in docs if item.get("kind") == "ScaledObject")
    triggers = {trigger["name"]: trigger for trigger in scaled_object["spec"]["triggers"]}
    assert set(triggers) == {"chronos-predictive", "frontend-http-cpu", "frontend-http-network"}
    cpu = triggers["frontend-http-cpu"]
    assert cpu["metricType"] == "AverageValue"
    assert cpu["metadata"]["threshold"] == "0.10"
    assert 'container="mak-container"' in cpu["metadata"]["query"]
    network = triggers["frontend-http-network"]
    assert network["metricType"] == "AverageValue"
    assert network["metadata"]["threshold"] == "150000"
    assert "container_network_transmit_bytes_total" in network["metadata"]["query"]
    assert "sum(irate(" in network["metadata"]["query"]
    assert "[1m]" in network["metadata"]["query"]


def test_backend_has_http_cpu_fallback_independent_of_queue_metrics():
    docs = render("components.frontend=false", namespace="backend")
    scaled_objects = [item for item in docs if item.get("kind") == "ScaledObject"]
    assert len(scaled_objects) == 5
    for scaled_object in scaled_objects:
        workload = scaled_object["spec"]["scaleTargetRef"]["name"]
        triggers = {item["name"]: item for item in scaled_object["spec"]["triggers"]}
        assert set(triggers) == {"queue", "chronos2-scaling", "http-cpu"}
        cpu = triggers["http-cpu"]
        assert cpu["metricType"] == "AverageValue"
        assert cpu["metadata"]["threshold"] == "0.25"
        assert f'container="{workload}"' in cpu["metadata"]["query"]


def test_smoke_stability_contract():
    docs = render(namespace="frontend")
    workloads = [
        item
        for item in docs
        if item.get("kind") in {"Deployment", "Rollout"}
        and item["metadata"]["name"]
        in {
            "userservice",
            "contacts",
            "balancereader",
            "ledgerwriter",
            "transactionhistory",
            "mak-app-rollout",
        }
    ]
    assert len(workloads) == 6
    for workload in workloads:
        assert workload["spec"]["template"]["metadata"]["annotations"] == (
            CHART_VALUES["workloadPodAnnotations"]
        )

    rollout = next(item for item in docs if item.get("kind") == "Rollout")
    assert "replicas" not in rollout["spec"]
    static_docs = render(
        "components.backend=false",
        "autoscaling.frontend.enabled=false",
        namespace="frontend",
    )
    static_rollout = next(item for item in static_docs if item.get("kind") == "Rollout")
    assert static_rollout["spec"]["replicas"] == 3

    scaled_objects = [item for item in docs if item.get("kind") == "ScaledObject"]
    assert len(scaled_objects) == 6
    for scaled_object in scaled_objects:
        scale_down = (
            scaled_object["spec"]["advanced"]["horizontalPodAutoscalerConfig"]
            ["behavior"]["scaleDown"]
        )
        assert scale_down["stabilizationWindowSeconds"] == 900


def test_finops_workflow_has_explicit_bank_mapping_and_fails_unknown_targets():
    workflow = (ROOT / ".github" / "workflows" / "finops-apply.yaml").read_text()
    helper = (ROOT / ".github" / "scripts" / "update-resource-requests.sh").read_text()
    assert "container_name:" in workflow
    assert "frontend/mak-app-rollout/mak-container)" in workflow
    assert ".resources.${DEPLOYMENT_NAME}.worker.requests" in workflow
    assert "지원하지 않는 리소스 식별자" in workflow
    assert ".github/scripts/update-resource-requests.sh" in workflow
    assert 'if [[ ! -f "$VALUES_FILE" ]]' in helper
    assert '${RESOURCES_PATH}.cpu // \\"MISSING\\"' in helper


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
