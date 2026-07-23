# Load test API Secret

부하·지연·오류·Queue 변경 API는 `X-Test-Token` 헤더를 요구합니다. Token은 Helm values나
Git에 저장하지 않고 배포 전에 Kubernetes Secret으로 생성합니다.

```bash
kubectl -n sample-fastapi create secret generic sample-fastapi-load-test \
  --from-literal=LOAD_TEST_TOKEN="$LOAD_TEST_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
```

Secret 참조는 `optional: true`라서 Secret 생성 전에도 일반 API와 Probe는 시작됩니다.
Secret이 없거나 `loadTest.existingSecret`을 빈 문자열로 설정하면 애플리케이션은 보호
API를 `503 Load test API is not configured`으로 차단합니다.
Secret은 보호 API를 제공하는 FastAPI Pod에만 주입하며 Worker에는 전달하지 않습니다.

KEDA scale-down 비교 테스트는 다음처럼 렌더링 값을 활성화합니다.

```yaml
worker:
  autoscaling:
    scaleDown:
      enabled: true
      stabilizationWindowSeconds: 60
```

기본값은 `enabled: false`이며 기존 Kubernetes HPA의 약 300초 안정화 동작을 유지합니다.
