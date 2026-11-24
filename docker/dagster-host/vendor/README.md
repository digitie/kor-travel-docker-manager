# Common core wholewheel 출처

- Origin: https://github.com/digitie/kor-travel-common/tree/f1faf56365854cefcc9e1f02f51c5b9c7e6a13ae/packages/py/kor-travel-common
- source/product REV: f1faf56365854cefcc9e1f02f51c5b9c7e6a13ae (Common #90, py 0.1.0.dev2 — GitHub prerelease `py-v0.1.0.dev2` 자산)
- dev2 변경: daemon 연결 풀 대기를 수명 잠금 밖으로(대기 1→10초), 크기·대기 env(`KOR_TRAVEL_DAGSTER_DAEMON_POOL_SIZE`·
  `_MAX_OVERFLOW`·`_TIMEOUT_SECONDS`, 기본 4·4·10), webserver storage pre-ping(opt-in `KOR_TRAVEL_DAGSTER_WEBSERVER_POOL_PRE_PING=1`).
  dev1(ec5e168)의 pool 2+2→4+4와 재시도 sensor 수정도 포함한다.
- daemon pool 제품92d5cf16ad85c2b6cdd0140226066374ae345917 / native submit 제품89291f36d731ab906161b96757522e1cd833b4ef.
- 파일: kor_travel_common-0.1.0.dev2-py3-none-any.whl
- workspace 갱신 제품29d6e7dacc49b9578822fe88d18205bafa238867. 기존 pool·native submit을 포함한다.
- SHA256: aa65a95efcd6e541903f3094947fe38f0fe1f1d521c6fdfde275809f8ca3b5c0 (release 자산 digest·SHA256SUMS와 같다)
- 크기: 83112B
- SPDX-License-Identifier: GPL-3.0-or-later; 저작권2026 Youn-sok Choi (digitie).
- Modified: 없음. LICENSE/NOTICE가 dist-info/licenses에 동봉된다.
- 독립 재빌드: f1faf56 Git archive를 python:3.12-slim의 uv 0.11.21로 `SOURCE_DATE_EPOCH=1795475640` 빌드한 wheel이 byte 동일하다.
- RECORD nonself26행 hash/size, package21개의 Git bytes와
  정식 daemon/webserver console entrypoint·SQLAlchemy plugin 둘(daemon pool·webserver pre-ping)을 대조했다.
- 상세 package SHA는 common-source.json이다. 수제 metadata/부분모듈 snapshot은 쓰지 않는다.
- 실제SDK/PG/신호·운영CPU·소비자 배포는 이 정적 검증과 별도다.
