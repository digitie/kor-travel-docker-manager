# 상태 조회 캐시 작업 재개

- 기준 main557: 0889d3b4207fcfa73890930da349fb4fc50077f4.
- 원본 draft533: c038a1dc07f02831bb883ec7cb403717484e5bc2. 이번 후속과 별도로 보존한다.
- 작업 branch: codex/manager-status-cache-20261026. 기존 main·원본 dirty 파일은 수정하지 않는다.
- 원반례: incomplete inventory 재사용, 지속 mutation에서 유한 재관측 실패가 없는 두 경우. 원2 FAIL XML은 외부 원장에 보존했다.
- 현재: Future 원본 별칭의 원3 FAIL을 수정한 뒤 집중37 PASS 및 정본 Ruff0.16.4 PASS. REST503/WS1013 고정 오류·실제 inventory 재수집을 검증했다. 전체 회귀·CI·독립 두 리뷰·실제 UI는 후속이다.
- 제품 동작·검증 범위는 [기능 규약](container-status-snapshot.md)이 정본이다. 새 운영 설치·기존533 close는 하지 않았다.
- 원6864657 로컬 전체는 새 반례 수정 전 1059 PASS/30 SKIP에서 정확 pytest PID만 중단했다. 최종 전체 PASS가 아니며 최종 exact CI를 확인해야 한다.
- 후속: follower는 get 입구의 고정5초 JOIN budget을 두 관측에 공유한다. pending만503/1013, 완료 경합·실제 수집 TimeoutError는 원객체를 보존한다. 원3a CI그린은 이전 세대이며 새 최종CI·후보UI는 별도다.

새 JOIN 경계는 snapshot/inventory/remote/WS 원검증을 합친 집중89 PASS와 정본 Ruff로 확인했다. 운영 장애나 leader 전체 종료 시간의 실측은 아니다.
