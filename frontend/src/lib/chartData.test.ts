import { describe, expect, it } from 'vitest';

import { maxPointsForWindow, mergeChartData } from './chartData';
import { MetricHistoryPoint } from './containerPresentation';

function point(second: number): MetricHistoryPoint {
  const stamp = new Date(Date.UTC(2026, 0, 1, 0, 0, 0) + second * 1000)
    .toISOString()
    .replace('T', ' ')
    .slice(0, 19);
  return { timestamp: stamp, cpu_pct: 1, mem_pct: 2, io_read: 0, io_write: 0 };
}

function series(count: number, startSecond = 0): MetricHistoryPoint[] {
  return Array.from({ length: count }, (_, i) => point(startSecond + i * 10));
}

describe('mergeChartData', () => {
  it('실시간 포인트가 없으면 히스토리를 그대로 반환한다', () => {
    const history = series(100);
    expect(mergeChartData(history, [], 24)).toBe(history);
  });

  it('GM-02 회귀: 24시간 히스토리가 실시간 포인트 1개 도착에도 보존된다', () => {
    // 24시간 = 8,640포인트(10초 샘플). 고정 360 상한이던 시절에는 이 병합이
    // 최근 1시간으로 무너졌다 — 헤더는 "최근 24시간"이라 쓰면서.
    const history = series(8640);
    const live = [point(8640 * 10)];

    const merged = mergeChartData(history, live, 24);

    expect(merged.length).toBe(maxPointsForWindow(24));
    expect(merged.length).toBeGreaterThanOrEqual(8640);
    // 최신 창을 보존한다: 실시간 포인트가 꼬리에 있다.
    expect(merged[merged.length - 1].timestamp).toBe(live[0].timestamp);
  });

  it('1시간 창의 기존 상한(360)은 그대로 유지된다', () => {
    const history = series(400);
    const live = [point(400 * 10)];

    const merged = mergeChartData(history, live, 1);

    expect(merged.length).toBe(360);
    expect(merged[merged.length - 1].timestamp).toBe(live[0].timestamp);
  });

  it('초과분은 오래된 쪽부터 버린다', () => {
    const history = series(360);
    const live = [point(360 * 10), point(361 * 10)];

    const merged = mergeChartData(history, live, 1);

    expect(merged.length).toBe(360);
    expect(merged[0].timestamp).toBe(history[2].timestamp);
  });

  it('같은 timestamp의 실시간 포인트는 중복 추가하지 않는다', () => {
    const history = series(10);
    const live = [history[9], point(100)];

    const merged = mergeChartData(history, live, 1);

    expect(merged.length).toBe(11);
    expect(merged.filter((p) => p.timestamp === history[9].timestamp).length).toBe(1);
  });

  it('시간 파싱 없이 문자열로만 다룬다 — zone 없는 UTC timestamp가 로컬에서 어긋나지 않는다', () => {
    // 형식이 다른 timestamp라도 병합·상한 로직은 파싱하지 않으므로 동작한다.
    const odd: MetricHistoryPoint = {
      timestamp: 'not-a-date',
      cpu_pct: 0,
      mem_pct: 0,
      io_read: 0,
      io_write: 0,
    };
    const merged = mergeChartData(series(5), [odd], 1);
    expect(merged[merged.length - 1]).toBe(odd);
  });
});

describe('maxPointsForWindow', () => {
  it('기간에 비례한다', () => {
    expect(maxPointsForWindow(1)).toBe(360);
    expect(maxPointsForWindow(6)).toBe(2160);
    expect(maxPointsForWindow(24)).toBe(8640);
    expect(maxPointsForWindow(72)).toBe(25920);
  });

  it('비정상 입력은 1시간으로 방어한다', () => {
    expect(maxPointsForWindow(0)).toBe(360);
    expect(maxPointsForWindow(Number.NaN)).toBe(360);
    expect(maxPointsForWindow(-5)).toBe(360);
  });
});
