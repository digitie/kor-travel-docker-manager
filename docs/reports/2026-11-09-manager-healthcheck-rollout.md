# 2026-11-09 Manager 상태 점검 배포와 첫 조회 개선 정산

이 문서는 PR #486·#488의 운영 적용, 기존 UI 실패와 후속 #584·#585를 구분한다.
아래 시각은 KST다. #584까지 남은 첫 UI 실패를 보존하고 #585의 두 호스트 설치·첫 UI 수용을 별도로 기록한다. 전체 track 완료 보고가 아니다.

## 소스 병합과 CI

| PR | 검증한 head | 병합 commit | 해당 head CI |
| --- | --- | --- | --- |
| [#488](https://github.com/digitie/kor-travel-docker-manager/pull/488) | `07b65475a0e4` | `4bfc66245c91` | backend 3,615 PASS·40 SKIP, frontend 47 PASS·type/lint/build 성공 |
| [#486](https://github.com/digitie/kor-travel-docker-manager/pull/486) | `8912a903a6c3` | `b19926445c8b` | backend 3,622 PASS·40 SKIP, frontend 47 PASS·type/lint/build 성공 |
| [#584](https://github.com/digitie/kor-travel-docker-manager/pull/584) | `434c0caeedf8` | `8dd7d7d90661` | backend 3,633 PASS·40 SKIP, frontend 47 PASS·type/lint/build 성공 |
| [#585](https://github.com/digitie/kor-travel-docker-manager/pull/585) | `5bc6d2b5b645` | `49cd6c480712` | backend 3,661 PASS·40 SKIP, frontend 47 PASS·type/lint/build 성공 |

- #488은 08:56:38, #486은 09:02:22에 정상 병합했다. 두 PR 모두 독립 적대 리뷰 2인 이상을 통과했다.
- CI 실행은 각각 `42536918728`, `42537326731`이다. 서로 겹치는 테스트 수를 합산하지 않는다.
- #488은 API listen·매핑·probe 포트를 같은 설정에서 유도하며 Common HTTP 격리 CLI를 사용한다.
- #486은 Weather·Geo code-server의 상태 조회에서 PID 종료·orphan DB 변경을 분리한다.
- 기존 run monitoring·재시도 경계와 다른 서비스 probe는 보존한다. live 성공이 자동 복구를 보장하지 않는다.

## Manager 설치

- n150·Odroid 모두 공식 trusted installer 정상 모드로 `b19926445c8b`를 설치한 뒤 후속 #584의 `8dd7d7d906611874461fdbf6105e5f167f1ec03e`로 정상 갱신했다.
- #584 검증 시 두 호스트의 Backend·Frontend가 활성이고 Backend `/health`는 200이었다.
- frontend 소스 차이가 없어 재빌드하지 않았다. 기존 frontend PID·환경 파일과 Manager `.env`를 보존했다.
- n150은 공식 execution rebind 후 `pin verify` 종료 코드 0과 source pin 보존을 확인했다. Docker 불변성은 아래 설치 세대별로 구분한다.
- Odroid 공식 설치는 성공했으나 후속 래퍼가 미설정 Map·PinVi registry에도 성공 응답을 요구해 `FAILED`를 기록했다.
- 해당 원 기록과 종료 코드를 보존했다. 별도 읽기 전용 검증에서 새 Backend PID·실행 경로·revision·200 응답을 확인했다.
- 이전·현재 코드 모두 같은 canonical registry 부재를 확인했고, 설정 경로 override도 없었다. Odroid는 Map·PinVi를 소유하지 않는다.
- 이를 기존 미설정 상태로 분류했다. registry 초기화·회전·불필요한 rebind·원 설치의 재시도는 하지 않았다.
- 후속 정상 설치는 n150 09:42:46, Odroid 09:47:31에 완료됐다. 양쪽 설치 결과는 `INSTALLED`이며 Docker·frontend·환경·source pin 보존을 확인했다.
- n150 pin 검증은 0이다. Odroid는 정확한 기존 registry 부재를 확인해 `NOT_CONFIGURED_UNCHANGED`로 기록했고 실제 종료 코드 2를 그대로 보존했다.
- #585의 n150 공식 설치는 10:19:01에 완료됐다. 새 `49cd6c` Backend 활성·pin 검증 0은 확인했으나 Docker 불변성 후속 검사 실패로 래퍼는 `FAILED`를 남겼다. 원 결과를 보존했다.
- 독립 읽기 전용 확인에서 현재 n150 소유 19개 컨테이너는 모두 설치 전에 생성·기동됐다. 원래 전체 전후 snapshot은 보존되지 않았고 events도 비어 정확히 어떤 변화였는지는 UNKNOWN이다. 외부 변화가 증명됐거나 전체 Docker가 불변이었다고 주장하지 않는다.
- #585 Odroid 공식 설치는 10:26:15에 `INSTALLED`로 끝났다. Docker·Manager/Frontend 환경·Frontend PID·source pin 보존을 확인했고 기존 registry 미설정의 실제 exit 2를 `NOT_CONFIGURED_UNCHANGED`로 분류했다.
- 최종 독립 읽기 전용 확인에서 두 호스트의 `49cd6c`·Backend/Frontend 활성·health 200과 설치된 strict C 파서·libyaml 6.0.3·TAB 선택 경계를 확인했다.
- 같은 최종 확인에서 Weather API·Weather/Geo code-server의 정확한 CID·이미지·기동 시각·healthy가 유지됐고 Weather 27개·Geo 6개·Transport 20개 예약 상태가 기준과 같았다.

## 소비 이미지와 점검 적용

| 역할 | 적용 시 이미지 ID 앞부분 | 이번 작업 |
| --- | --- | --- |
| Weather API | `f3f7a4a85873` | 기존 Common HTTP 설정 유지, 이미지·컨테이너 교체 없음 |
| Weather code-server | `68c093c9fed2` | 같은 이미지로 Common gRPC 격리 live 점검 적용 |
| Geo code-server | `28b099e3bc74` | 같은 이미지로 Common gRPC 격리 live 점검 적용 |

- 실제 소비 이미지의 Common 모듈·gRPC 의존성과 live CLI 호출을 확인했다. 이 작업에서 이미지를 재빌드하지 않았다.
- API는 이미 Python `-I`, 90초 주기·Docker 12초·4회 설정으로 healthy였다. 소스 병합을 새 이미지 배포로 세지 않는다.
- 두 code-server는 Python `-I`, 90초 주기·10초 timeout·5회·600초 기동 유예·5초 기동 점검 간격을 유지했다.
- Geo의 수집 튜닝·환경·mount·자원 제한은 보존했다. 공유 daemon·webserver·DB 및 Transport는 재생성하지 않았다.
- Geo 최초 적용 래퍼의 배열 순서 비교 실패와 stdin Compose 진단 label 변화는 원 결과에 보존했다.
- 별도 읽기 전용 대조에서 원본 Compose와 현재 환경·mount·점검 시간의 의미가 같음을 확인했다. 최초 실패를 exit 0으로 고치지 않았다.
- 최종 두 code-server는 healthy이며 workspace가 LOADED다. workspace server ID가 현재 실제 server ID와 일치했다.
- 공식 Weather·Geo current release 검증은 healthy였고, 등록·resolved Compose·실제 소유 runtime 이미지 ID가 일치했다.
- 이미지 ID가 바뀌지 않아 불필요한 release 재등록은 하지 않았다.

## code-server 점검 적용 창의 수집·실행 세대 보존

- Weather 재생성 전 native 상태 26개를 보관했다. 수집 run 취소·SQL 수정은 하지 않았고, 예약은 공식 SDK로 일시 중지·복원했다.
- WeatherAPI current와 KMA 특보가 각각 native SUCCESS로 자연 종료했다. 실행 시간은 302초·611초였다.
- 두 app sync도 성공이었다. WeatherAPI는 요청 150건·값 1,650개, KMA 특보는 요청 1건·값 2,902개를 기록했다.
- origin 기준 Weather 실행·대기 0과 해당 두 실행 ID의 worker 부재를 확인한 뒤 code-server를 재생성했다.
- 이후 native 26개의 identity·상태가 정지 직전 snapshot과 정확히 같음을 확인했다. cursor는 진행할 수 있도록 덮어쓰지 않았다.
- effective 27개는 RUNNING 26·STOPPED 1이다. STOPPED는 `weather_native_incarnation_recovery`다.
- `weather_export_publisher`는 정지 직전 실측 snapshot과 복원 후 모두 RUNNING이다. 이번 작업에서 새로 활성화한 것이 아니다.
- Geo·Transport 예약 상태는 앞선 실제 읽기 전용 snapshot과 같았다.
- 이 code-server 점검 적용 창에는 전체 컨테이너 55개 중 두 대상 CID·시작 시각만 바뀌었다. 나머지 53개와 55개 이미지 ID는 동일했다. 이 증거를 후속 #585 n150 전체 Docker 불변성으로 확대하지 않는다.

## 실제 공개 UI와 미해결 항목

- 변경 전 n150 UI 8항목 PASS. Odroid는 기능 8항목을 수행했지만 컨테이너 API 503 두 건으로 전체 FAIL이었다.
- 첫 배포 후 n150 UI 8항목 PASS. Odroid는 기능 8항목을 수행했지만 `/api/v1/containers` 503 한 건으로 전체 FAIL이었다.
- 로그인·대시보드·목록·WebSocket·로그아웃·인증 해제·로그아웃 후 재연결을 검사했다. 각 브라우저를 닫고 쿠키를 제거했다.
- 기능 수행 성공을 전체 UI PASS로 바꾸지 않았다. Manager 설치와 code-server 점검 적용만으로 이 503이 해결됐다고 주장하지 않는다.
- 최종 #585 설치 뒤 10:27:03의 새 backend 첫 공개 UI는 n150·Odroid 각각 8 PASS, 총 16/16이었다. 이번 작업자의 목록 사전 조회·해당 smoke 재시도 없이 각 한 번 검사했다.
- 양쪽 모두 JS 오류·HTTP 5xx·차단한 쓰기 0, WS 수신 2프레임·연결 1개였고 로그아웃 후 재연결·잔여 쿠키 0, 브라우저 종료를 확인했다. 앞선 세대 FAIL을 이 결과로 덮어쓰지 않았다.

## 후속 PR #584 — 설치와 단일 실측 완료, 첫 UI 실패 보존

- 컨테이너 조회의 5초 JOIN timeout을 확인했다. 별도 프로세스 수집 한 번은 42행·4.1061초였고 Compose 파싱은 1.3280초였다.
- 실제 Compose 233,819바이트의 단일 파싱 표본은 Python SafeLoader 1.4345초, CSafeLoader 0.1318초이며 모델이 같았다.
- #584 head `434c0caeedf8b4cdae5135f22c6499d1fa547b12`는 `get_compose_config`만 안전한 C 파서 우선으로 바꾼다.
- libyaml 미설치 시 SafeLoader를 쓰고 호출마다 현재 파일을 다시 읽는다. 새 캐시·스레드·재시도·timeout·상태 판정 변경은 없다.
- 작성자 집중 회귀 175 PASS, 독립 적대 리뷰 2인 PASS를 확인했다. 독립 parser 회귀 11 PASS와 실제 원문의 모델 동등성도 확인했다.
- CI `42540794137`이 성공했고 09:41:17에 #584를 정상 병합했다. 위 두 호스트의 최신 설치본에서 CSafeLoader 선택 코드와 libyaml 가용성을 확인했다.
- 최신 설치 후에도 두 code-server CID·이미지·healthy, Weather effective 27개 상태가 유지됐다. 이후 새 backend 첫 Odroid UI는 목록 503 두 건으로 다시 FAIL이었다. 기능 8개 완료를 전체 PASS로 바꾸지 않았다.
- 별도 프로세스의 설치 후 수집은 42행이었다. 전체 4.1061→3.2948초, Compose 파싱 1.3280→0.1227초이며 실제 daemon의 병행 요청·캐시·부하를 통제한 비교가 아니다.
- 단일 파싱 표본을 전체 조회 절감률·CPU 사용률 개선·운영 503 해결로 확대하지 않는다.

## 후속 PR #585 — 두 호스트 설치와 첫 UI 수용

- CPU 할당 제한·상위 cgroup throttling 가설을 기각했다. #584 뒤 남은 cold 경로는 보호 환경 필드 lookup 캐시의 첫 파생에서 `runtime_topology`의 reference Compose를 엄격한 Python 파서로 다시 읽는 부분이었다.
- 같은 reference 233,819바이트·literal TAB 0개에 Python 중복 키 파서 1.4154초, 동일 생성자의 C 안전 파서 0.1223초와 실제 모델 동등성을 확인했다. 단일 표본을 일반 응답 시간·CPU 비율로 바꾸지 않는다.
- 공개 `UniqueKeySafeLoader`는 기존 Python 기반을 유지한다. private C 안전 로더에 같은 중복 키 생성자를 등록하고 TAB 없는 문자열에만 사용한다.
- literal TAB·비문자열은 파싱 **전에** 기존 Python 경로를 선택한다. 정상 quoted·block TAB은 보존하며 C 파싱 오류를 다른 로더로 재시도하지 않는다.
- 중복·merge 충돌·bool/숫자의 의미상 같은 키·해시 불가능 키·unsafe 객체 태그 거부와 dispose를 보존한다. 캐시·스레드·시간 제한·음성 상태 판정 변경은 없다.
- 관련 집중 229 PASS·독립 적대 리뷰 2인 PASS, 추가 독립 33개 회귀·5개 경계 표본을 확인했다. 겹치는 검증을 합산하지 않는다.
- 알려진 P3 경계는 고립 surrogate escape 같은 잘못된 Unicode를 C가 기존 Python보다 엄격하게 거절한다는 점이다. 실제 유효 설정과 핵심 거부 계약을 확인했으며 모든 YAML 문법의 완전 동등성을 주장하지 않는다.
- 정확한 head의 CI `42544156683`이 성공했고 10:18:05에 `49cd6c480712b2ebdf5f70c316b65ec8d2003c70`으로 정상 병합했다.
- 최종 `49cd6c`의 두 호스트 실제 설치와 위 첫 공개 UI 16/16을 수용했다. n150 래퍼의 정확한 전체 Docker 변화는 UNKNOWN으로 남긴다. 따뜻해진 조회의 재실행으로 첫 수용을 대신하지 않았다.

## 증거 보존

다음은 저장소 밖의 로컬 미추적 감사 파일명이다. 비밀·운영 접속값·원문 환경·private 절대 경로는 이 문서에 싣지 않는다.

- `checkpoint.json`: 두 소스 CI·병합 결과. 운영 부분은 후속 최종 증거로 대체해 읽는다.
- `odroid-install-recovery-classification-ro.json`: 설치 후 래퍼 실패와 별도 실제 설치 확인의 구분.
- `geo-post-health-ro-proof.json`, `final-current-workspace-proof.json`: 현재 semantic 설정·healthy·server ID 대조.
- `weather-drain-progress-20261109T002134Z.safe.json`, `weather-drain-origin-final-ro.json`: 두 자연 종료와 origin 기준 drain.
- `final-operations-acceptance.json`: 점검 적용 창의 55개 실행 세대·원래 26개 상태·공식 release 검증. 초기 STOPPED 가정과 달리 publisher는 실제 RUNNING이었다.
- `live-ui-baseline-46395049.json`, `live-ui-post-deployment-820042a0.json`, `live-ui-final-584-45885abe.json`: 원래·첫 배포·#584 설치 뒤 UI 실패 보존.
- `odroid-status-profile-ro.json`, `odroid-compose-parser-ro.json`, `pr584-independent-review.json`: 단일 실측과 후속 소스 검토 경계.
- `final-584-runtime-delta-ro.json`: #584 두 설치본·C 안전 파서·예약·기존 소비 이미지 보존. 후속 첫 UI와 수집 실측은 포함하지 않는다.
- `strict-parser-5bc6-independent-review.json`: #585의 정확한 후보·실제 설정 모델 동등·중복/객체/TAB/fallback/dispose 경계 검토.
- `n150-585-install-classification-ro.json`, 두 최종 설치 결과, `live-ui-final-585-first-32c8bc6a.json`: 실제 설치·미확정 전체 변화·최종 첫 UI를 구분한다.
- `final-585-runtime-delta-ro.json`: 최종 10개 범위 검사 통과. 현재 설치·정확한 소비 runtime·예약 보존과 n150 전체 변화 UNKNOWN을 함께 기록한다.
