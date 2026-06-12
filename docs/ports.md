# 로컬 포트 정책

이 문서는 TripMate 계열 로컬 개발 포트 정책과 2026-06-12 기준 관련 로컬 레포에서 확인한 변경 전 포트, 변경 후 정책 포트를 정리한다.

---

## 1. 기본 규칙

- 로컬 서비스 포트는 `12000`부터 시작한다.
- Docker/app target마다 `100` 단위 대역을 배정한다.
- 대역 순서는 `config/docker-targets.yml`의 `dependency_order`와 같다.
- API 포트는 각 대역의 `+1`을 사용한다.
- 같은 서비스 자체에서 추가로 필요한 API/관리 포트는 `+2`부터 사용한다.
- Web UI 포트는 각 대역의 `+5`를 사용하고, 추가 Web UI는 `+6`부터 사용한다.
- PostgreSQL 접속 포트는 예외적으로 표준 `5432`를 사용한다.
- `tripmate-manager` 자체 포트는 dependency 순서와 무관하게 `12900-12999` 대역을 사용한다.
- 이 문서의 포트 정책은 호스트에서 노출되는 로컬 포트 기준이다. 컨테이너 내부 포트는 이미지 표준 포트를 유지할 수 있다.

---

## 2. 대역 배정

| Target | 대역 | 정책상 사용 포트 | 비고 |
|---|---:|---|---|
| `db` | `12000-12099` | 없음 | PostgreSQL은 표준 `5432` 고정이므로 이 대역은 비워 둔다. |
| `storage` | `12100-12199` | API `12101`, Web UI `12105` | RustFS S3 API와 console. |
| `geo` | `12200-12299` | API `12201`, Web UI `12205` | `python-kraddr-geo` REST API와 admin UI. |
| `map` | `12300-12399` | API `12301`, 추가 관리 포트 `12302`, Web UI `12305` | `python-krtour-map` admin API, Dagster, admin Web UI. |
| `ai` | `12400-12499` | API `12401`, 추가 MCP 포트 `12402`, Web UI `12405` | `tripmate-agent` API, MCP HTTP, Web UI. |
| `main` | `12500-12599` | API `12501`, Web UI `12505` | `tripmate` API/Web. |
| `tripmate-manager` | `12900-12999` | API `12901`, Web UI `12905` | dependency 추가와 무관하게 고정. |

---

## 3. 관련 로컬 레포 포트 조사

조사 대상 canonical 레포는 `F:\dev\tripmate`, `F:\dev\tripmate-agent`, `F:\dev\python-kraddr-geo`, `F:\dev\python-krtour-map`, `F:\dev\tripmate-manager`다. `*-codex`, `*-claude`, `*-antigravity` 등 worktree 복제본은 같은 계약이 중복될 수 있어 본 표의 집계 대상에서 제외했다.

| 레포 | 대상 | 변경 전 확인 포트 | 변경 후 정책 포트 | 근거 |
|---|---|---:|---:|---|
| `tripmate-manager` | Backend API | `9091` | `12901` | `backend/src/tripmate_manager/main.py`, `docs/dev-environment.md` |
| `tripmate-manager` | Dashboard Web UI | `9092` | `12905` | `frontend/package.json` |
| `tripmate-manager` | RustFS S3 API | `9003` | `12101` | `.env.example`, `docker-compose.yml` |
| `tripmate-manager` | RustFS console | `9004` | `12105` | `.env.example`, `docker-compose.yml` |
| `tripmate-manager` | 통합 PostgreSQL | `15434` | `5432` | PostgreSQL 표준 포트로 변경 |
| `python-kraddr-geo` | PostgreSQL | `15434` | `5432` | `.env.example`, `docker-compose.yml`, `AGENTS.md` |
| `python-kraddr-geo` | REST API | `9001`, 일부 문서 `8888` | `12201` | `README.md`, `docker/api.Dockerfile`, `CLAUDE.md` |
| `python-kraddr-geo` | Admin Web UI | `9002`, 일부 문서 `13088` | `12205` | `README.md`, `CLAUDE.md` |
| `python-kraddr-geo` | RustFS S3 API/console | `9003` / `9004` | `12101` / `12105` | `README.md`, `CHANGELOG.md` |
| `python-krtour-map` | Standalone PostgreSQL | `15433` | `5432` | 통합 DB 사용 시 `5432`로 이관 대상. |
| `python-krtour-map` | Admin API | `9011` | `12301` | `.env.example`, `docker-compose.yml` |
| `python-krtour-map` | Dagster Webserver | `9013` | `12302` | `.env.example`, `docker-compose.yml` |
| `python-krtour-map` | Admin Web UI | `9012` | `12305` | `.env.example`, `docker-compose.yml`, frontend package |
| `python-krtour-map` | kraddr-geo 연동 API URL | `9001` | `12201` | `.env.example`, `docs/address-geocoding.md` |
| `python-krtour-map` | RustFS S3 API/console | `9003` / `9004` | `12101` / `12105` | `.env.example`, `docker-compose.yml` |
| `tripmate-agent` | API | `9041` | `12401` | `.env.example`, `docker-compose.yml`, `README.md` |
| `tripmate-agent` | MCP HTTP | `8010` | `12402` | `.env.example`, `docker-compose.yml` |
| `tripmate-agent` | Web UI | `9042` | `12405` | `.env.example`, `docker-compose.yml`, frontend package |
| `tripmate-agent` | PostgreSQL | `15434` | `5432` | `.env.example`, `docker-compose.yml` |
| `tripmate-agent` | RustFS S3 API/console | `9003` / `9004` | `12101` / `12105` | `.env.example`, `docker-compose.yml` |
| `tripmate` | API | `8001`, app compose `18082` | `12501` | `.env.example`, `infra/docker-compose.app.yml` |
| `tripmate` | Web UI | `3001`, app compose `13082`, infra compose `23000` | `12505` | `.env.example`, `apps/web/package.json`, `infra/docker-compose*.yml` |
| `tripmate` | PostgreSQL | `55432` | `5432` | 구 분리 DB 기준. 통합 DB 모델로 이관 대상. |
| `tripmate` | RustFS S3 API/console | `9003` / `9004` | `12101` / `12105` | `.env.example`, `infra/docker-compose*.yml` |

---

## 4. `tripmate-manager` 반영 상태

| 항목 | 반영 파일 | 상태 |
|---|---|---|
| PostgreSQL host 포트 `5432` | `.env.example`, `docker-compose.yml`, `config/docker-targets.yml` | 반영 |
| RustFS host API `12101`, console `12105` | `.env.example`, `docker-compose.yml`, `config/docker-targets.yml` | 반영 |
| RustFS 컨테이너 내부 API `9000`, console `9001` | `.env.example`, `docker-compose.yml`, `scripts/ensure-rustfs-buckets.sh` | 반영 |
| `python-kraddr-geo` API `12201`, Web UI `12205` | `.env.example`, `docker-compose.yml`, `config/docker-targets.yml`, `scripts/infra.sh` | 반영 |
| Manager Backend API `12901` | `backend/src/tripmate_manager/main.py`, `frontend/src/components/DashboardClient.tsx` | 반영 |
| Manager Web UI `12905` | `frontend/package.json` | 반영 |
| 포트 대역 metadata | `config/docker-targets.yml` | 반영 |
| 관련 레포 현재/변경 포트 문서화 | `docs/ports.md` | 반영 |

---

## 5. 후속 원칙

- 관련 프로젝트 레포의 실제 설정 변경은 각 레포에서 별도 PR로 진행한다.
- `tripmate-manager`는 통합 DB/RustFS의 기준 포트를 먼저 제공하고, 관련 레포 문서는 이 표의 정책 포트를 따르도록 순차 정리한다.
- 새 서비스가 생기면 `config/docker-targets.yml`의 `dependency_order`에 추가하고, `base + n * 100` 대역을 배정한다.
