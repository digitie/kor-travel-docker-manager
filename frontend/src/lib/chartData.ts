import { MetricHistoryPoint } from '@/lib/containerPresentation';

/** 백엔드 metrics collector의 샘플 주기(초). 포인트 수 상한 계산의 기준이다. */
export const METRIC_SAMPLE_INTERVAL_SECONDS = 10;

/** 선택 기간(시간)이 담을 수 있는 최대 포인트 수. */
export function maxPointsForWindow(chartHours: number): number {
  const hours = Number.isFinite(chartHours) && chartHours > 0 ? chartHours : 1;
  return Math.ceil((hours * 3600) / METRIC_SAMPLE_INTERVAL_SECONDS);
}

/**
 * 히스토리 조회 결과와 WebSocket 실시간 포인트를 병합한다.
 *
 * 상한은 **선택 기간에 비례**해야 한다. 고정 360(1시간치)으로 자르면 24시간을
 * 선택해도 첫 실시간 포인트가 도착하는 순간 차트가 최근 1시간으로 무너진다 —
 * 헤더는 "최근 24시간"이라 쓰면서 실제로는 1시간만 그리는 오판 유도 상태였다.
 * 초과분은 오래된 쪽부터 버려 선택 기간의 최신 창을 보존한다.
 *
 * timestamp 문자열 비교로 중복만 제거하고 시간 파싱은 하지 않는다 — 백엔드
 * timestamp는 zone 표기 없는 UTC 문자열이라 naive Date 파싱은 로컬 zone에서
 * 어긋난다.
 */
export function mergeChartData(
  history: MetricHistoryPoint[],
  live: MetricHistoryPoint[],
  chartHours: number,
): MetricHistoryPoint[] {
  if (live.length === 0) return history;

  const merged = [...history];
  const seen = new Set(history.map((point) => point.timestamp));
  for (const point of live) {
    if (!seen.has(point.timestamp)) {
      merged.push(point);
      seen.add(point.timestamp);
    }
  }

  const cap = maxPointsForWindow(chartHours);
  if (merged.length > cap) {
    return merged.slice(merged.length - cap);
  }
  return merged;
}
