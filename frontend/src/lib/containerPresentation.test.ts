import { describe, expect, it } from 'vitest';
import { ContainerStatus, visibleLocalContainers } from './containerPresentation';

const item = (id: string, status: string, extra: Partial<ContainerStatus> = {}): ContainerStatus =>
  ({ id, name: id, status, state: status, ports: [], ...extra });

describe('로컬 실행 원장', () => {
  const rows = [item('api', 'running'), item('old', 'exited'), item('never', 'not_created'),
    item('other-host', 'up', { remote: true }), item('unknown', 'running', { observed_only: true }),
    item('retrying', 'restarting'), item('unavailable', 'offline')];

  it('기본 목록은 실행 중·재시작 중이며 미등록 실행 컨테이너도 포함한다', () => {
    expect(visibleLocalContainers(rows).map((row) => row.id)).toEqual(['api', 'unknown', 'retrying']);
  });
  it('중지 항목 포함에도 미생성·다른 호스트·연결 불가 선언은 제외한다', () => {
    expect(visibleLocalContainers(rows, true).map((row) => row.id)).toEqual(['api', 'old', 'unknown', 'retrying']);
  });
});
