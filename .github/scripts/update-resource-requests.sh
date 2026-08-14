#!/usr/bin/env bash
set -euo pipefail

: "${VALUES_FILE:?VALUES_FILE is required}"
: "${RESOURCES_PATH:?RESOURCES_PATH is required}"
: "${CPU:?CPU is required}"
: "${MEMORY:?MEMORY is required}"

if [[ ! -f "$VALUES_FILE" ]]; then
  echo "::error::${VALUES_FILE}를 찾을 수 없습니다."
  exit 1
fi

current_cpu="$(yq -r "${RESOURCES_PATH}.cpu // \"MISSING\"" "$VALUES_FILE")"
current_memory="$(yq -r "${RESOURCES_PATH}.memory // \"MISSING\"" "$VALUES_FILE")"

if [[ "$current_cpu" == "MISSING" || "$current_memory" == "MISSING" ]]; then
  echo "::error::${VALUES_FILE}의 ${RESOURCES_PATH}.cpu 또는 memory 경로가 없습니다."
  echo "::error::대상 Chart의 실제 resources.requests 구조와 workflow 매핑을 확인하세요."
  exit 1
fi

# 동일한 권장값을 다시 승인하면 yq를 실행하지 않습니다. yq -i는 대상 값이 같아도
# block scalar 등 관계없는 YAML 서식을 바꿀 수 있으므로 사전 비교가 필요합니다.
if [[ "$current_cpu" == "$CPU" && "$current_memory" == "$MEMORY" ]]; then
  echo "변경 사항 없음: ${VALUES_FILE} ${RESOURCES_PATH}는 이미 cpu=${CPU}, memory=${MEMORY}입니다."
  exit 0
fi

yq -i "${RESOURCES_PATH}.cpu = strenv(CPU) | ${RESOURCES_PATH}.memory = strenv(MEMORY)" "$VALUES_FILE"

updated_cpu="$(yq -r "${RESOURCES_PATH}.cpu // \"MISSING\"" "$VALUES_FILE")"
updated_memory="$(yq -r "${RESOURCES_PATH}.memory // \"MISSING\"" "$VALUES_FILE")"

if [[ "$updated_cpu" != "$CPU" || "$updated_memory" != "$MEMORY" ]]; then
  echo "::error::리소스 요청값 검증 실패: cpu=${updated_cpu}, memory=${updated_memory}"
  exit 1
fi

echo "수정 완료: ${VALUES_FILE} ${RESOURCES_PATH} -> cpu=${CPU}, memory=${MEMORY}"
