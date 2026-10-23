# 공유 daemon workspace 갱신 주기 선택

Common44의 머지된 전체 wheel을 고정해 공유 daemon만 metadata 갱신180초를 선택할 수 있게
한다. 기본60초는 최적화 비활성이며 daemon 서비스의 ON/OFF와는 관계없다. 원 SDK의240초
실패 유예를 유지하므로180초 선택 시 freshness 상한은420초다. heartbeat·sensor tick·run
monitoring·RPC timeout·원 CLI 및 신호 처리·DB pool 정책은 바꾸지 않는다.

## 구현 범위

- Common의 immutable merge `b4f7f3dcb5f95c682b027e3886a603d5d02625d3`에서 정식 wholewheel을
  만들고 wheel hash·모든 package 파일/RECORD·이미지 내용 tag를 다시 결박한다.
- `KOR_TRAVEL_DAGSTER_WORKSPACE_RELOAD_SECONDS`는 공용 daemon에만 전달한다. 미설정은60,
  명시180은 갱신3배/상한420이다. 빈 값·그 밖의 값은 원 SDK에 진입하기 전 거절한다.
- 공유 webserver·storage-migrate도 같은 고정 이미지의 wheel을 쓰지만 이 옵션을 받지 않는다.
  Weather547의 false-default export flag·공유 RW volume은 보존한다.

## 운영 순서와 롤백

실제 변경은 운영 담당자가 두 독립 리뷰·CI 이후 수행한다. 이 PR 자체는 운영을 변경하지 않는다.

1. 공식 trusted installer로 최종 merged Manager SHA를 설치한다. canonical root는
   `/opt/kor-travel-docker-manager`이며 NVMe의 절대 release symlink와 root-owned
   source/wheelhouse를 유지한다. release root 옵션은 없고 현재 symlink의 대상 부모에서 유도한다.
2. Odroid는 기본 설치 모드, CLI만 남긴 n150은 기존 웹·API inactive를 확인한 `--cli-only`다.
   기본 설치는 Manager backend를 재시작하므로547만 먼저 설치할 필요가 없다면 이번 최종 SHA로
   한 번 설치한다. installer와 plane 명령은 각각 자체 host lock을 잡으므로 외부 lock과 중첩하지 않는다.
3. 필요 시 운영 `.env`에180을 명시한다. 공용 plane 이미지는 Docker data-root/build cache/
   임시 경로가 NVMe인지 확인한 뒤 준비한다. 실제 경로와 장치 일치는 운영 실측 사항이다.
4. 호스트 profile의 공식 plane 절차로 dry-run을 확인하고 적용한다. `dagster-plane converge`는
   rehearsal/rebuildable profile 전용이며 daemon-only 옵션이 없다. 이미지가 바뀌면 공유
   daemon·webserver 둘 다 교체될 수 있다. code-server·worker·공유 PG는 대상이 아니다.
   기존 worker와 schedule/sensor 상태는 보존·검증하고 daemon을 중지 상태로 남기지 않는다.
5. env60 또는 미설정으로 되돌린 뒤 같은 공식 plane 절차로 재생성하면 원60/300 동작으로 복귀한다.
   이미지 자체를 되돌릴 때는 검토한 installer로 이전 Manager SHA를 재설치하고 이전 env를
   복원한 뒤 plane을 수렴한다. n150은 롤백도 `--cli-only`를 유지한다. 컨테이너 안 pip 교체나
   symlink 수동 전환은 사용하지 않는다.

CLI/storage/shared-DSN·heartbeat·SIGTERM·sensor·동일 입력의 CPU/RSS/신선도 최소3쌍 실측은
운영 수용 gate다.420초는 실제 wall deadline이 아니며 code location 자동 반영은 최대 약120초
늦어질 수 있다. consumer 소스 검증과 운영 CPU 개선을 구분한다.
