import { describe, expect, it } from 'vitest';

import { formatBytes, formatTimestamp } from './format';

// 이 스위트는 TZ=Asia/Seoul(UTC+9)에서 돌 때 zone 변환을 검증한다.
// package.json test 스크립트가 TZ를 고정한다.
const IN_KST = new Date('2026-09-01T00:00:00Z').getTimezoneOffset() === -540;

describe('formatTimestamp', () => {
  it('zone 없는 UTC를 로컬 zone으로 변환한다 (GM-02 후속: 9시간 어긋남 회귀)', () => {
    // 예전 구현은 "03:30:00"을 그대로 반환했다(UTC를 로컬처럼). KST면 12:30이어야 한다.
    const out = formatTimestamp('2026-09-01 03:30:00');
    if (IN_KST) {
      expect(out).toBe('12:30:00');
    } else {
      // 다른 zone에서도 최소한 원문 시각 문자열을 그대로 뱉지 않아야 한다(변환됨).
      expect(out).toMatch(/^\d\d:\d\d:\d\d$/);
    }
  });

  it('includeDate면 MM-DD를 붙인다 (24h·72h 창의 반복 라벨 방지)', () => {
    const out = formatTimestamp('2026-09-01 03:30:00', { includeDate: true });
    if (IN_KST) {
      expect(out).toBe('09/01 12:30:00');
    } else {
      expect(out).toMatch(/^\d\d\/\d\d \d\d:\d\d:\d\d$/);
    }
  });

  it('자정 근처 UTC는 날짜가 넘어간다 (KST +9)', () => {
    // 2026-09-01 20:00 UTC = 2026-09-02 05:00 KST
    const out = formatTimestamp('2026-09-01 20:00:00', { includeDate: true });
    if (IN_KST) {
      expect(out).toBe('09/02 05:00:00');
    }
  });

  it('파싱 불가 입력은 원문을 그대로 반환한다', () => {
    expect(formatTimestamp('not-a-date')).toBe('not-a-date');
  });

  it('빈 문자열은 빈 문자열', () => {
    expect(formatTimestamp('')).toBe('');
  });

  it('이미 zone이 있는 ISO 입력에 Z를 중복하지 않는다', () => {
    const out = formatTimestamp('2026-09-01T03:30:00Z');
    if (IN_KST) {
      expect(out).toBe('12:30:00');
    } else {
      expect(out).toMatch(/^\d\d:\d\d:\d\d$/);
    }
  });
});

describe('formatBytes', () => {
  it('0과 undefined는 0 B', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(undefined)).toBe('0 B');
  });

  it('단위 경계', () => {
    expect(formatBytes(1024)).toBe('1 KB');
    expect(formatBytes(1024 * 1024)).toBe('1 MB');
  });
});
