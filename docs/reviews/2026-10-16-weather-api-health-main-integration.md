# Weather API HTTP health 통합 소스 리뷰 기록

## 검토된 후보

기존 PR488 2d208ad2와 main 384cb40을 통합한 25a1faa9187c321668de7f536f122fa26443fea9를
root와 C가 서로 원문을 읽지 않고 독립 검토했다. SOURCE_PASS, 열린 P1·P2는 0이다.

| 검토자 | 범위·직접 실행 | 원문 SHA256 |
|---|---|---|
| root | 전체 5파일·부모·소스 해시·전체 YAML·main보존, 실제계약 3 PASS | 21afe1c3fa3639c24f1080d2b24882f737797fd60672f01a955137b66fcaa403 |
| C | 전체 5파일·상류 13파일 경계·파일/YAML 59조건, 독립 계약 3 PASS | cb185df01e5962f0510f5b895bc3cf91272ce3b327086f2851d2a32da80d6ab7 |

[root 원문](evidence/2026-10-16-weather-api-health-main-reviewer-root.md)과
[C 원문](evidence/2026-10-16-weather-api-health-main-reviewer-c.md)은 byte 그대로 보존했다.
검증 수는 서로 겹치므로 합산하지 않는다. 작성자 로컬 290·설치기 25·frontend 35는 별도다.

25a1faa의 [CI38859139398](https://github.com/digitie/kor-travel-docker-manager/actions/runs/38859139398)는
backend 2914 PASS/37 SKIP(422.37초), frontend 35/type/lint/build 모두SUCCESS로 종결했다.
SKIP을PASS에 포함하지 않는다. 최초 설치기 7 FAIL은 isolated python의 구제품 import 환경
원문을 보존하고 전용 환경 25 PASS로 분리했다.

## 후속 main 통합

그 CI 실행 중 main에 별도 PR492가 병합되어 새 main fa56de930b107bc5d8fdf84625d7e0bcdaba8024를
51ff1aa7d0878a5b1be895bbe549bd8c05f3cffd로 추가 통합했다. registry 기반 Prometheus·자기
호스트 좌표 검사 등 상류 변경을 그대로 받았다. API488의 Compose 전체와 계약 테스트는25a1faa와
바이트 동일하며 main 기준 YAML은 기존 API 5개 필드의 의미 변경만 다르다. backend/src·config·scripts·
frontend는 fa56과 바이트 동일하여 고아 worker·hung-owner·host·환경 보호를 되돌리지 않는다.
journal 충돌은 양쪽 원문을 보존하고 488 기록 2개를 안정된 위치로 옮겨 정산했다.

추가 통합에서 로컬 집중 회귀 399 PASS(52.20초)와 Ruff를 확인했다. 25a1faa의 독립 리뷰·CI는 해당
후보에 고정된 이력이며 새 main·이 문서 HEAD의 CI나 최종통합 검토를 대신하지 않는다.
제품 바이트 보존 근거와 최종 diff는 별도 checkpoint로 인계하며 새 HEAD CI는 해당 SHA에서 확인한다.
이 문서 추가는 제품·테스트·Compose 변경 0이다.

## 운영·병합 경계

Common CLI가 설치된 소비 이미지가 선행이며 strict 2xx의 생존과 DB/provider 준비·복구는 별개다.
HTTP 3초·Docker 12초·30초 주기·4회는 보존하고 명목 120초를 정확한실제 탐지 시간으로 주장하지 않는다.
이 소스 통합에서 운영 배포·서비스 변경·PR 머지는 0이고 PR486도 보류 그대로다.
실제 API488 HC 양성은 별도 증거이며 CPU/RSS 절감·최종 UI 전체 검증을 뜻하지 않는다.
PR488 draft의 최종 병합은 현재 HEAD CI·후속 통합 판단·운영 완료 gate를 root가 정산한다.
