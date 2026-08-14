# JWT 및 Frontend Canary 변경 절차

이 변경은 Chart 정적 계약만 준비한다. 실제 Secret 생성, Argo CD 자동 동기화 통제,
Gateway API 플러그인 배포와 Rollout 조작은 승인된 작업 창에서 수행한다.

## 코드 계약

- Chart는 JWT Secret이나 JWT 키 값을 생성하지 않는다.
- Frontend와 Backend는 `secrets.existingSecret`에 지정된 기존 Secret만 참조한다.
- 현재 기본 이름은 `jwt-key`이며 Terraform이 JWT 회전을 활성화하면
  `bank-jwt-key-v2`로 명시적으로 덮어쓴다.
- Secret에는 `jwtRS256.key`와 `jwtRS256.key.pub`가 있어야 한다.
- Frontend는 공개키, `userservice`는 개인키와 공개키, 나머지 Backend는 공개키를 마운트한다.
- Frontend Rollout은 `mak-app-active`를 stable Service로,
  `mak-app-preview`를 canary Service로 사용한다.
- `mak-app-route`의 Backend weight는 Gateway API 플러그인이 조정한다.

## 예상 Argo CD 차이

1. Chart가 관리하던 `jwt-key` Secret 리소스가 렌더링에서 제거된다.
2. 모든 Bank Pod의 JWT volume이 `secrets.existingSecret`을 참조한다.
3. Rollout의 `activeService`/`previewService`가
   `stableService`/`canaryService`로 변경된다.
4. `argoproj-labs/gatewayAPI` traffic router가 추가된다.
5. HTTPRoute는 stable/canary Service를 각각 100/0 weight로 참조한다.
6. Argo CD는 Rollout이 변경하는 HTTPRoute weight만 diff에서 제외한다.

## 적용 전 확인

1. Frontend와 Backend Namespace에 전환 대상 Secret을 먼저 준비한다.
2. Secret key 이름만 확인하고 값은 출력하지 않는다.
3. Rollouts 컨트롤러의 Gateway API 플러그인과 HTTPRoute patch 권한을 확인한다.
4. 현재 Application, Rollout, Service, HTTPRoute YAML을 롤백 기준으로 보관한다.
5. Frontend와 Backend 자동 동기화를 통제한 상태에서 Application과 Chart를 함께 전환한다.

## 롤백

- Secret 문제 시 과거에 노출된 키를 재사용하지 않고 새 버전 Secret을 생성한다.
- Chart Revision을 되돌리기 전에 해당 Revision이 참조하는 Secret이 두 Namespace에
  존재하는지 확인한다.
- Rollout 오류 시 traffic routing을 중단하고 직전 정상 Rollout, Service, HTTPRoute를 복원한다.
- Pod Ready, 인증 기능과 HTTPRoute 상태를 확인한 후 자동 동기화를 복구한다.

## 정적 검증

```bash
helm lint charts/mak-app
helm template mak-frontend charts/mak-app --namespace frontend \
  --set components.backend=false --set secrets.existingSecret=bank-jwt-key-v2
helm template mak-backend charts/mak-app --namespace backend \
  --set components.frontend=false --set secrets.existingSecret=bank-jwt-key-v2
python3 -m pytest -q tests/test_mak_app_chart.py
```

렌더링 결과에는 `jwtPrivateKey`, `jwtPublicKey`, PEM 본문 또는 JWT Secret 리소스가
포함되면 안 된다.
