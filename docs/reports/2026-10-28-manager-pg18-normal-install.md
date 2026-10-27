# 2026-10-28 Odroid Manager PG18 호환 설치와 공개 UI 수용

## 현재 수용 상태

- Odroid Manager를 공식 전체 설치기로 `aeb2d34ce276e5e219761a62cd4b1c8f39cad72a`에서 `f368af3e7d24d831d8160619eb95087708f42b55`로 갱신했다. 설치기 자체는 221.815초에 exit 0으로 끝났고, 실제 소스 436개·새 backend PID·그 PID의 listener·상태 확인 API 200을 확인했다.
- `f368af3e`는 PG18 제품 `d1468213` 뒤의 문서 변경이다. n150은 이미 `d1468213`의 웹·API·CLI가 실행 중이고 제품이 같으므로 재설치하지 않았다.
- 공용 PostgreSQL 18 전환은 앞선 외부 작업이다. 이번 작업은 Manager 설치이며 DB 업그레이드, 컨테이너 재생성, 공용 plane 수렴 작업을 수행하지 않았다.
- 공개 UI 12개 검증은 90.696초에 통과했다. 현재 관리·관측 범위의 Docker 실행 행 19개의 이름·CID·상태가 같은 브라우저의 실제 API 응답과 일치했다. 수치 메트릭은 유한한 비음수임을 검사했으며 서로 다른 시각의 CPU 수치를 동일하다고 주장하지 않는다.

## 공식 설치와 실패 이력

기존 원본 작업 경로를 수정하지 않고 NVMe의 새 관리자 소유 소스 경로를 준비했다. Git tree 436개와 기존 ARM 오프라인 wheel 26개의 지문·권한을 검산했으며 새 패키지를 다운로드하거나 원 cache를 수정하지 않았다. thin bundle의 선행 커밋이 객체로만 존재해 검증이 거부된 첫 실패와 donor fetch 실패를 보존했다. 새 소스 clone 안에만 정확한 선행 커밋의 참조를 만들고 bundle을 검증하는 최소 복구가 통과했다. 원 donor의 HEAD·참조와 서비스는 변경하지 않았다.

정본 `scripts/install-ktdm-trusted-release` 369줄 전체를 실행했다. 부분 실행이나 수동 symlink 전환은 하지 않았다. 설치기 SHA256은 `3c2046787e37d09a5a3138267394bc92ee935117b59520c6c75f1581808e41ab`다. 내부 840초 제한과 독립 systemd unit의 930초·종료 20초, CPU 1개·메모리 1536MiB·swap 0·PID 256 상한으로 실행했다. SSH 연결이 끊겨도 같은 unit의 자식 전체가 종료 대상이다.

**설치 후 검사기의 전체 결과는 처음에 실패였다.** 설치는 정상 종료했지만, 뒤이은 `ktdctl targets validate --json`이 25초 제한에 걸렸다. 원 receipt의 `pass_all=false`와 unit의 exit 1을 성공으로 고쳐 쓰지 않았다. 설치기를 재실행하거나 같은 검사를 기한만 늘려 재시도하지 않았다. 지연 원인은 확정하지 않았다.

다음 두 근거를 별도로 수용했다.

1. 공식 설치기는 symlink 전환 전에 새 release와 복사한 동일 환경으로 정본 `targets validate`를 호출하고 성공해야만 계속한다(`scripts/install-ktdm-trusted-release` 213~219줄). 설치기 exit 0에는 이 전환 전 검사의 성공도 포함된다.
2. 뒤이은 Transport 공식 stage가 자체 필수 `targets validate` 60초 검사를 포함해 정상 통과했다. 40.166초는 **stage 전체** 소요 시간이며 CLI 단독 시간이 아니다. 이 후속 검사를 기존 25초 실패와 구분한다.

설치 자식은 회수했고 그 process group의 부재를 확인했다. 같은 Invocation의 작업 unit은 MainPID 0·빈 cgroup을 확인한 뒤 그 unit만 정리했으며, 최종 `not-found`·MainPID 0·cgroup 부재를 확인했다. 관리자 소유 원장과 실패 로그는 보존했다.

## 보존 검사와 한계

별도 읽기 전용 후속 검사에서 다음을 확인했다.

- 기존 영속 Docker 60개의 CID·이미지·실행 상태·시작 시각·재시작 수가 전후 동일했다. 실행 행 19개만 비교한 공개 API 검증과 집합이 다르다.
- 공유 Dagster daemon·webserver 두 컨테이너의 실제 CID·mount source·컨테이너 안 workspace/instance 내용이 전후 같았다. 정본 `show`의 Odroid max 5와 지문도 같았다. `show`의 desired 설정과 이미 실행 중인 plane의 실제 설정을 같은 것으로 취급하지 않았다.
- 새 소스 clone과 기존 wheel 26개는 같았고 이전 release도 남아 있었다. 프론트엔드 unit·PID·작업 경로는 그대로였다. 다른 서비스 이미지·job·instigator와 daemon을 조작하지 않았다.
- 현재 release의 환경파일은 보존된 이전 release의 환경파일과 byte 단위로 같았다. **설치 전 환경파일과 프론트엔드 산출물 지문은 검사기 RAM에만 있었고, 25초 실패가 후속 비교보다 먼저 발생했다.** 따라서 이 원장만으로 설치 전 환경파일·산출물의 지문이 영속화되었다고 주장하지 않는다. 환경파일은 native 복사 계약과 보존된 이전 파일의 동일성, 프론트엔드는 동일 unit·PID와 새 빌드 부재를 각각 근거로 삼았다.
- 이후 공개 UI 검사에서 현재 프론트엔드 소스 58개·빌드 manifest 4개·정적 자산 17개가 기존 수용 산출물과 같고 실제 TLS로 제공됨을 추가 확인했다. 이것은 설치 후 현재 산출물 검증이며 누락된 과거 지문을 소급 생성한 것이 아니다.

스냅샷은 해당 관측 구간의 동일성을 뜻하며 미래의 다른 운영자 변경을 배제하지 않는다. 이번 작업에서 ARM CPU·메모리 절감이나 수집 성능 개선을 측정하지 않았다.

## 공개 UI 실제 검증

Odroid의 실제 인증 구현으로 비밀번호를 첫 HTTP 전에 검증했다. 입력은 숨김 TTY와 RAM/stdin만 사용했고 비밀·쿠키·원본 DTO를 원장이나 저장소에 기록하지 않았다. 브라우저 호스트 n150의 관리자 비밀번호와 대상 Odroid의 인증을 결합하지 않았다. SSH 중계 파일은 비밀 입력 전과 대상 접속 전에 기존 정본 지문으로 검증했다.

n150의 기존 고정 브라우저 런타임을 사용했으며 테스트 중 새 이미지나 프론트엔드를 만들지 않았다. 12개 검증은 로그인 폼, 실제 로그인 200·Secure/HttpOnly cookie, 컨테이너 원장, WebSocket 두 frame 이상, 인증된 사용자·컨테이너 조회, reload 세션 유지, 실제 로그아웃 200, 로그인 화면 복귀, 보호 API 401, 로그아웃 후 socket 재연결 반복 부재, 오류·미허용 쓰기 부재다. 동일 컨테이너 GET에서 정본 managed·observed scope의 실행 행 19개를 실제 Docker와 대조했다. 정적 TLS 자산 17개는 총 1,400,417 byte였다.

로그인·로그아웃 POST는 각각 한 번이고 서버 세션 철회를 확인했다. 소유 브라우저 프로세스 부재·프로필 제거·호스트 runtime 전후 동일성을 확인했다. 브라우저 작업 unit의 실제 관측값은 `not-found`, MainPID 0, 빈 ControlGroup이다. HAProxy는 변경하지 않았고 기존 SameSite 예외 사항은 이번 범위에서 고치지 않았다.

## 원장 지문

민감한 운영 원장은 저장소 밖에 보존하며 아래는 해당 원장의 식별 지문이다.

| 범위 | SHA256 |
| --- | --- |
| 새 NVMe 소스 준비 복구 | `defeee6d821cd79b30f135cb34fff8b7a54573731fe4f1ac2754955381d89d0b` |
| 설치·원래 후속 검사 실패·unit 결과 | `79626269b18eccfd0fd80cdaf0c294044a3c0ac0f9b488163666efc88d914a7d` |
| 별도 읽기 전용 보존 검사 | `b96fd1ba6b0d7bc7492879b799a5e6649dcd5b8316a12a6d594ef3d395e46931` |
| 설치 작업 unit 정리 | `990510bac76be4fd2005dc50abc63c7b02dc690f1cea58c778446a9eabc2d788` |
| 후속 공식 stage와 CLI 검사 수용 | `13e66c9a039f77b8eee617dfebae3858c95c1d4b5c811b2939e4066e125fa210` |
| 공개 UI 실제 12개 | `e542bf39381f2ec55f7ad98559493cfdb19144fd5971e00011ceeeb925528192` |
| 공개 UI 소유 unit 부재 관측 | `e4608dfee1214297637246185d4c085b1e454324c1f469a2e13c304bf379ef6a` |

정본 instance 소비와 이후 서비스별 재구축은 각 서비스의 별도 수용 절차다. 이번 Manager 설치·공개 UI 성공을 Transport의 새 이미지 배포 완료나 공용 plane 재생성으로 확대하지 않는다.
