/** 화면 표시용 포맷터. 값을 바꾸지 않고 읽는 방식만 바꾼다. */

export function formatBytes(bytes: number | undefined, decimals = 1): string {
  if (bytes === undefined || bytes === 0) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

/** 차트 X축·툴팁용 시각. 파싱에 실패하면 **원문을 그대로** 돌려준다 — 표시 헬퍼가
 * 예외를 던지면 그 축 하나 때문에 화면 전체가 죽는다.
 *
 * 백엔드 timestamp는 zone 표기 없는 UTC `"YYYY-MM-DD HH:MM:SS"`다. UTC로 파싱해 뷰어
 * 로컬 zone으로 변환한다 — 예전에는 공백 split 후 시각 문자열을 그대로 반환해 UTC
 * 시각이 로컬처럼 표시됐고(KST에서 9시간 어긋남), 03:30 KST 스파이크가 18:30으로
 * 라벨링됐다. 여러 날에 걸친 창(24h·72h)은 시각만으로는 반복 라벨이 되므로
 * `includeDate`로 날짜를 붙인다. */
export function formatTimestamp(
  timestampStr: string,
  options: { includeDate?: boolean } = {},
): string {
  if (!timestampStr) return '';
  try {
    const normalized = timestampStr.includes(' ')
      ? timestampStr.replace(' ', 'T')
      : timestampStr;
    // 이미 zone 지정이 있으면 그대로, 없으면 UTC로 간주해 Z를 붙인다.
    const withZone = /([zZ]|[+-]\d\d:?\d\d)$/.test(normalized)
      ? normalized
      : normalized + 'Z';
    const d = new Date(withZone);
    if (Number.isNaN(d.getTime())) return timestampStr;
    const time = d.toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
    if (!options.includeDate) return time;
    const date = d.toLocaleDateString([], { month: '2-digit', day: '2-digit' });
    return `${date} ${time}`;
  } catch {
    return timestampStr;
  }
}
