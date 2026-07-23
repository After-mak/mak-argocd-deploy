"""Helm 실행 전에도 핵심 배포·KEDA 안전 계약이 유지되는지 확인합니다."""

from pathlib import Path

import yaml


CHART_DIR = Path(__file__).resolve().parents[1]
TEMPLATES = CHART_DIR / "templates"


def test_safe_scale_down_default_and_queue_recovery_values():
    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))

    assert values["worker"]["autoscaling"]["scaleDown"] == {
        "enabled": False,
        "stabilizationWindowSeconds": 60,
    }
    assert values["queue"]["maxRetries"] == 3
    assert values["queue"]["visibilityTimeoutSeconds"] == 60


def test_worker_replica_and_hpa_behavior_are_conditional():
    deployment = (TEMPLATES / "worker-deployment.yaml").read_text(encoding="utf-8")
    scaled_object = (TEMPLATES / "worker-scaledobject.yaml").read_text(encoding="utf-8")

    assert "if not .Values.worker.autoscaling.enabled" in deployment
    assert "replicas: {{ .Values.worker.replicaCount }}" in deployment
    assert "if .Values.worker.autoscaling.scaleDown.enabled" in scaled_object
    assert "stabilizationWindowSeconds:" in scaled_object


def test_only_api_references_external_secret():
    api = (TEMPLATES / "fastapi-deployment.yaml").read_text(encoding="utf-8")
    worker = (TEMPLATES / "worker-deployment.yaml").read_text(encoding="utf-8")

    assert ".Values.loadTest.existingSecret" in api
    assert "secretRef:" in api
    assert "optional: true" in api
    assert ".Values.loadTest.existingSecret" not in worker
    assert "secretRef:" not in worker


def test_redis_host_remains_required_and_recovery_config_is_rendered():
    configmap = (TEMPLATES / "configmap.yaml").read_text(encoding="utf-8")

    assert 'required "redis.host must be supplied by Terraform/Argo CD"' in configmap
    assert "QUEUE_MAX_RETRIES:" in configmap
    assert "QUEUE_VISIBILITY_TIMEOUT_SECONDS:" in configmap
    assert "WORKER_JOB_TIMEOUT_SECONDS:" in configmap
