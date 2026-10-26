## 2026-10-27 — 양 호스트 baremetal 설치 완료

- 최종 제품85101과 같은 tree의 main M `aeb2d34c`를 n150·Odroid에 공식 정상 설치했다. 제품 CI backend3445 PASS/40 SKIP·frontend PASS, 두 독립 리뷰·후보 UI14 뒤 실제 양 호스트 공개 TLS12개씩을 수용했다.
- n150 show는 기존6/raw bytes를 유지하고 Odroid show는 prepared5 파일을 검증한다. 현재 살아 있는 Odroid plane의 c444 bind는 별개이며 재생성하지 않았다. root alias와 container bind inode의 의미를 혼동하지 않는다.
- 새 ARM frontend의 실제 빌드·승격, current Docker19개씩의 공개 API 정합, 원503과 진단기 실패 보존은 [운영 기록](reports/2026-10-27-manager559-two-host-normal-install.md)에 정산했다.
- 다음 실제 단계는 외부 Transport139의 정본 show 소비와 별도 명시적 plane/CS 적용이다. Weather 이미지/실행 job을 이 설치에서 바꾸지 않았다. 아래의 운영 전·CI 미완 문장은 해당 과거 checkpoint이며 현재 수용 범위는 이 절을 따른다.

## 2026-10-26 — 기존 Odroid 튜닝 보존 후속

- 074a의 backend3424 PASS/40 SKIP·frontend PASS는 원소스 결과로 보존한다. 현재 운영과 비교해 tag 네 한도/로그 수준의 추가 보존이 필요하여 typed host 정본으로 반영했다. n150 기본 bytes/path/digest와 기존 파일/G/읽기 권한은 그대로다.
- 원제품 positive3 FAIL → 첫 구현 base key-only 거부3 FAIL/51 PASS → 현재 직접54 PASS/외부 호출0을 분리한다. 이 후속 제품의 두 독립 리뷰·새 CI·후보 UI·실제 설치는 미완이다. 기존074a 후보 UI 준비를 새 제품 수용으로 계산하지 않는다.
- normal installer는 명시 파일 준비만 하고 현재 plane을 재시작하지 않는다. 새 effective 파일과 운영 파일의 byte동등은 주장하지 않으며 공식 후속 적용 때 기본 새 안전 한도와 기존 튜닝 보존을 검산한다.

# 호스트별 공용 Dagster 구성 재개

- 기준 main6dd의 별도 Linux 작업트리에서 n1506/Odroid5 보존을 구현했다. 원558 작업트리와 원본 dirty 저장소는 보존한다. 제품 정본/파일 권한/외부 경합은 [기능 계약](host-dagster-instance-config.md), 실제 절차는 [운영 가이드](prod-deployment.md#호스트별-공용-instance-상한-보존)를 따른다.
- 직접 순수60 PASS/외부 호출0. 원 wholebackend 602P3F/560.72초 중단에는 로컬 owned PG와 이미지 빌드가 실제 있었다. 격리 mount 기본값 해석을 source 정렬했지만 실제 통합 재실행은 미수용이다. 정확3 project의 container/network/volume 부재 RO 증거와 원 실패를 감사 원장에 보존했다.
- 운영 SSH·설치·서비스 변경0. 최종 제품 두 독립 리뷰·정확 CI·실제 설치/공개 UI는 별도다. Transport139는 Manager 설치 뒤 공식 show를 소비하며 prepare를 자동 실행하지 않는다. 상한 감축/CPU 효과/외부 전체 G 원자성은 주장하지 않는다.
