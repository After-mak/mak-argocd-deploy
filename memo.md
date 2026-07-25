# 유효한 2048비트 RSA 키쌍 생성
openssl genrsa -out jwtkey.pem 2048
openssl rsa -in jwtkey.pem -pubout -out jwtcert.pem

# 개인키 전체 출력 (이 전체 텍스트를 secret.yaml의 jwtkey.pem 에 넣음)
cat private.pem

# 공개키 전체 출력 (이 전체 텍스트를 secret.yaml의 jwtcert.pem 에 넣음)
cat public.pem


# 직접 노출 꺼리면 쿠버네티스상에 secret으로 관리
kubectl delete secret jwt-key -n default --ignore-not-found
kubectl create secret generic jwt-key -n default \
  --from-file=jwtkey.pem=jwtkey.pem \
  --from-file=jwtcert.pem=jwtcert.pem \
  --from-file=jwtpubkey.pem=jwtcert.pem

> 정상적으로 적용되면 secret/jwt-key configured (또는 created) 라는 문구

# 로컬의 임시 키 파일 삭제
openssl genrsa -out jwtkey.pem 2048
openssl rsa -in jwtkey.pem -pubout -out jwtcert.pem

