import {
  Activity,
  BarChart3,
  Boxes,
  Database,
  FolderGit2,
  Gauge,
  Radio,
  ServerCog,
} from 'lucide-react';

/** `GET /api/v1/containers`의 요소. */
export interface ContainerStatus {
  id: string;
  name: string;
  display_name?: string;
  role?: string;
  connection?: string;
  public_url?: string;
  expected_ports?: string[];
  image?: string;
  status: string;
  state: string;
  ports: string[];
  metrics?: {
    timestamp: string;
    cpu_pct: number;
    mem_pct: number;
    mem_usage: number;
    mem_limit: number;
    io_read: number;
    io_write: number;
  };
  config?: {
    ports: string[];
    env: Record<string, string>;
    volumes: string[];
    networks: string[];
    /** 배포 계약이 값을 고정한 환경변수 이름. 서버가 저장 시점에 변경을 거부하므로
     * 편집기도 처음부터 잠근다 — 눌러 본 뒤에야 거부를 알게 하지 않는다. */
    locked_env?: string[];
  };
}

export interface MetricHistoryPoint {
  timestamp: string;
  cpu_pct: number;
  mem_pct: number;
  io_read: number;
  io_write: number;
}

// 상태 칸은 Docker의 원문(running/not_created…)을 그대로 보여 줬는데 같은 화면의 KPI는
// 한국어라 어휘가 이중이었다. 표시는 한국어로 통일하고 원문은 title 속성으로 남긴다.
const STATUS_LABELS: Record<string, string> = {
  running: '실행 중',
  exited: '중지됨',
  offline: '연결 끊김',
  paused: '일시정지',
  restarting: '재시작 중',
  starting: '시작 중',
  created: '생성됨',
  not_created: '미생성',
  'not created': '미생성',
  dead: '비정상 종료',
  error: '오류',
};

/** 매핑에 없으면 **원문을 그대로** 돌려준다 — 새 상태값이 생겨도 화면이 비지 않는다. */
export const statusLabel = (status: string): string =>
  STATUS_LABELS[(status || '').toLowerCase()] ?? status;

// role은 내부 식별자(map-api, metrics-exporter…)라 처음 보는 사람에게 의미가 없다.
const ROLE_LABELS: Record<string, string> = {
  postgresql: '데이터베이스',
  rustfs: '오브젝트 저장소',
  prometheus: '메트릭 수집',
  grafana: '메트릭 시각화',
  cadvisor: '컨테이너 메트릭',
  'metrics-exporter': '메트릭 노출',
};

/** 분기 순서가 곧 우선순위다.
 *
 * 넓은 패턴(`api`, `ui`)을 먼저 두면 좁은 패턴이 도달하지 못한다 — 예: `geocoder-api`는
 * `-api`에, `geocoder-ui`는 `ui`에 먼저 걸려 "지오코더"가 영원히 나오지 않는다. 좁은
 * 것부터 검사한다. 이 순서를 바꿀 때는 그 사실을 먼저 확인하라. */
export const roleLabel = (role: string | null | undefined): string => {
  const value = (role || '').toLowerCase();
  if (!value) return '-';
  if (ROLE_LABELS[value]) return ROLE_LABELS[value];
  // `map-postgresql`·`concierge-postgresql`처럼 접두사가 붙은 실제 role 값은 exact key와
  // 맞지 않는다(config/docker-targets.yml). 접미사로 판정한다.
  if (value.endsWith('postgresql') || value.endsWith('postgres')) return '데이터베이스';
  if (value.includes('geocoder')) return '지오코더';
  if (value.includes('dagster') || value.includes('scheduler')) return '워크플로';
  if (value.includes('mcp')) return 'MCP 서버';
  if (value.endsWith('-api') || value.includes('api')) return 'API 서버';
  if (value.includes('ui') || value.includes('web')) return '웹 UI';
  return role || '-';
};

export const getStatusConfig = (status: string) => {
  const s = status.toLowerCase();
  if (s === 'running') {
    return {
      dotClass: 'bg-ok animate-pulse',
      textClass: 'text-ok font-semibold',
      rowClass: 'bg-card hover:bg-subtle',
    };
  } else if (s === 'exited' || s === 'offline') {
    return {
      dotClass: 'bg-danger',
      textClass: 'text-danger font-semibold',
      rowClass: 'bg-danger/5 hover:bg-subtle',
    };
  } else if (s.includes('starting') || s.includes('restarting') || s.includes('paused')) {
    return {
      dotClass: 'bg-warn animate-ping',
      textClass: 'text-warn font-semibold',
      rowClass: 'bg-card hover:bg-subtle',
    };
  } else {
    return {
      dotClass: 'bg-disabled',
      textClass: 'text-secondary font-semibold',
      rowClass: 'bg-card hover:bg-subtle',
    };
  }
};

/** 아이콘과 표시명. `roleLabel`과 같은 이유로 좁은 패턴부터 검사한다. */
export const getContainerPresentation = (container: ContainerStatus) => {
  const role = container.role || '';
  const id = container.id || '';

  if (role === 'postgresql' || id.includes('postgresql')) {
    return { Icon: Database, displayName: container.display_name || 'PostgreSQL (PostGIS)' };
  }
  if (role === 'rustfs') {
    return { Icon: FolderGit2, displayName: container.display_name || 'RustFS Store' };
  }
  if (role.includes('geocoder')) {
    return { Icon: ServerCog, displayName: container.display_name || 'Kor Travel Geo' };
  }
  if (role.includes('mcp')) {
    return { Icon: Radio, displayName: container.display_name || 'MCP HTTP' };
  }
  if (role.includes('scheduler') || role.includes('dagster')) {
    return { Icon: Activity, displayName: container.display_name || 'Workflow' };
  }
  if (role.includes('concierge') || role.includes('map-api') || role.includes('pinvi-api')) {
    return { Icon: ServerCog, displayName: container.display_name || 'App API' };
  }
  if (role.includes('ui') || role.includes('web')) {
    return { Icon: Boxes, displayName: container.display_name || 'Web UI' };
  }
  if (role === 'prometheus') {
    return { Icon: Activity, displayName: container.display_name || 'Prometheus 메트릭 저장소' };
  }
  if (role === 'grafana') {
    return { Icon: BarChart3, displayName: container.display_name || 'Grafana 시각화 도구' };
  }
  if (role === 'metrics-exporter') {
    return { Icon: Gauge, displayName: container.display_name || 'cAdvisor Exporter' };
  }

  return { Icon: Boxes, displayName: container.display_name || container.name };
};
