# ARCHITECTURE — TripMate Manager 아키텍처

이 문서는 `tripmate-manager`의 시스템 아키텍처와 컴포넌트 간 데이터 흐름을 다룬다.

---

## 1. 개요

`tripmate-manager`는 TripMate 서비스를 구동하기 위한 인프라 서비스(PostgreSQL, RustFS 등)의 Docker 컨테이너 구동 상태를 모니터링하고 제어하는 시스템이다.

```mermaid
graph TD
    subgraph Frontend [Next.js Dashboard Web]
        UI[Dashboard UI / Status & Controls]
        TQ[TanStack Query / API Polling]
        UI --> TQ
    end

    subgraph Backend [FastAPI Service]
        API[FastAPI Endpoints /api/containers]
        DS[DockerService Wrapper]
        API --> DS
    end

    subgraph Infrastructure [Docker Daemon / Host]
        D_Sock[docker.sock / Named Pipe]
        C_PG[PostgreSQL / PostGIS Container]
        C_RFS[RustFS Container]
        
        DS -->|API Calls / Controls| D_Sock
        D_Sock -->|Manage| C_PG
        D_Sock -->|Manage| C_RFS
    end
    
    TQ -->|HTTP requests| API
```

---

## 2. 백엔드 설계 (Python FastAPI)

백엔드는 가볍고 빠른 API 서빙을 위해 Python FastAPI를 채택한다. 로컬/원격 Docker 데몬과의 통신은 `docker` Python SDK를 사용한다.

### 2.1 Docker 데몬 연동 (`DockerService`)
- **연동 방식**: `docker.from_env()`를 호출하여 환경변수 및 기본 소켓 경로를 참조해 Docker 클라이언트를 초기화한다.
- **Windows 호스트**: 명명된 파이프 (`npipe:////./pipe/docker_engine`)를 통해 Docker Desktop 데몬과 통신한다.
- **Linux/WSL**: 유닉스 소켓 (`unix:///var/run/docker.sock`)을 통해 통신한다.
- **예외 처리**: Docker 데몬이 구동 중이지 않거나 권한이 없을 경우를 대비해, API 응답 시 503 Service Unavailable 및 정형화된 에러 객체를 반환하도록 설계한다.

### 2.2 API 엔드포인트 설계
- `GET /api/containers`: 관리 대상 컨테이너(postgresql, rustfs)의 구동 상태(상태 코드, CPU/메모리 실시간 사용량 간략 정보) 목록 반환.
- `POST /api/containers/{name}/action`: 컨테이너 제어 명령 (`start`, `stop`, `restart`) 실행.
- `GET /api/containers/{name}/logs`: 최근 100라인의 stdout/stderr 컨테이너 로그 반환.

---

## 3. 프론트엔드 설계 (Next.js & React)

프론트엔드는 Next.js 14+ App Router를 기반으로 구성하며, 실시간 대시보드 성격의 단일 페이지 애플리케이션(SPA) 형태로 운영한다.

### 3.1 상태 관리 및 데이터 동기화
- **TanStack Query (React Query)**: 백엔드 API와의 통신 및 캐싱을 전담한다. 대시보드 상태를 유지하기 위해 5초 단위의 폴링(`refetchInterval: 5000`)을 적용하여 인프라 상태 변화를 실시간으로 대시보드에 반영한다.
- **Zod & React Hook Form**: 컨테이너의 설정(예: 포트 번호, 환경변수, 데이터 볼륨 경로) 변경 양식을 안전하게 검증하고 전송한다.

### 3.2 UI/UX 디자인 시스템
- **Rich Aesthetics**: 어두운 테마(Dark mode) 기반의 글래스모피즘(Glassmorphism) 스타일을 채택한다.
- **상태 인디케이터**: 컨테이너 상태에 따라 부드러운 네온 그라데이션 글로우 효과를 부여한다 (예: 구동 중 `emerald-500` 빛남, 중지됨 `rose-500`, 작업 진행 중 `amber-500` 깜빡임).
- **마이크로 애니메이션**: 컨테이너 제어 버튼 클릭 시 로딩 스피너 및 클릭 피드백 전환 효과를 적용하여 사용자 조작감을 극대화한다.

---

## 4. 데이터베이스 및 파일 스토리지 (대상 인프라)

`tripmate-manager`가 관리하는 Docker 컨테이너 정의는 다음과 같다:

1. **TripMate PostgreSQL / PostGIS**:
   - 컨테이너: `tripmate-postgres`
   - 이미지: `postgis/postgis:16-3.5-alpine`
   - 목적: TripMate 공간 데이터 및 일반 데이터 보관.
   - 내부 포트: `5432` / 외부 노출 포트: `55432`.
2. **python-kraddr-geo PostgreSQL / PostGIS**:
   - 컨테이너: `kraddr-geo-postgres`
   - 이미지: `postgis/postgis:16-3.5`
   - 목적: `python-kraddr-geo`의 T-027 최종 적재 DB와 후속 검증 DB 구동.
   - 내부 포트: `5432` / 외부 노출 포트: `15434`.
   - 기본 DSN: `postgresql+psycopg://addr:addr@localhost:15434/kraddr_geo`.
   - 기본 pgdata: `KRADDR_GEO_PGDATA=/home/digitie/kraddr-geo-data/pgdata-final-20260529`.
3. **RustFS**:
   - 컨테이너: `tripmate-rustfs`
   - 이미지: `rustfs/rustfs:latest`
   - 목적: 미디어 자원과 `python-kraddr-geo` 업로드 원천 보관을 위한 공용 S3 호환 오브젝트 스토리지.
   - 포트: `9003` (API), `9004` (어드민 콘솔).
   - 기본 credential: `RUSTFS_ACCESS_KEY=rustfsadmin`, `RUSTFS_SECRET_KEY=rustfsadmin`.
   - 기본 bucket: `tripmate-media`, `kraddr-geo`.

`python-kraddr-geo`는 더 이상 자체 저장소의 Docker compose 또는 RustFS 구동 스크립트로 PostgreSQL/RustFS 생명주기를 직접 관리하지 않는다. 로컬에서 해당 인프라를 정지하거나 재시작할 때는 이 저장소의 `scripts/infra.sh` 또는 대시보드/API를 사용한다.
