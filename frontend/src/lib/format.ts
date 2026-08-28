/** 화면 표시용 포맷터. 값을 바꾸지 않고 읽는 방식만 바꾼다. */

export function formatBytes(bytes: number | undefined, decimals = 1): string {
  if (bytes === undefined || bytes === 0) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

/** 차트 X축용 시각. 파싱에 실패하면 **원문을 그대로** 돌려준다 — 표시 헬퍼가 예외를
 * 던지면 그 축 하나 때문에 화면 전체가 죽는다. */
export function formatTimestamp(timestampStr: string): string {
  if (!timestampStr) return '';
  try {
    const parts = timestampStr.split(' ');
    if (parts.length === 2) {
      return parts[1];
    }
    const d = new Date(timestampStr.replace(' ', 'T') + 'Z');
    return d.toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  } catch {
    return timestampStr;
  }
}
