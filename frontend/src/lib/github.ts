/** GitHub compare/commit 링크를 **브라우저에서만** 만든다.
 *
 * 호스트에서 diff를 계산할 이유가 없다(설계 P4). 고정 SHA와 실행 중 revision만
 * 있으면 브라우저가 GitHub으로 보내면 되고, 그러면 백엔드 코드도 egress도 0이다.
 * 이전 설계는 별도 scratch mirror에 fetch하는 방안이었는데, 트리거 표면만 넓히고
 * 비전문 관리자가 정작 읽지도 않는 diff를 위해 위험을 늘리는 선택이었다.
 */

const CANONICAL_HOSTS = new Set(['github.com', 'www.github.com']);
const REVISION = /^[0-9a-f]{40}$/;

/** `https://github.com/owner/repo.git` → `https://github.com/owner/repo`.
 *
 * canonical GitHub 호스트가 아니면 `null`이다 — 임의 URL을 링크로 만들면 payload가
 * 곧 링크 생성기가 된다. */
function repositoryBase(remoteUrl: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(remoteUrl);
  } catch {
    return null;
  }
  if (parsed.protocol !== 'https:' || !CANONICAL_HOSTS.has(parsed.hostname)) return null;
  const path = parsed.pathname.replace(/\.git$/, '').replace(/\/+$/, '');
  // `/owner/repo` 두 조각만 허용한다.
  if (!/^\/[^/]+\/[^/]+$/.test(path)) return null;
  return `https://github.com${path}`;
}

export function buildGithubCommitUrl(remoteUrl: string, revision: string): string | null {
  const base = repositoryBase(remoteUrl);
  if (!base || !REVISION.test(revision)) return null;
  return `${base}/commit/${revision}`;
}

/** 두 revision 사이의 비교 링크. 값이 같거나 형식이 틀리면 `null`이라 UI가 링크를
 * 아예 렌더하지 않는다 — 눌러도 아무것도 안 나오는 링크는 없느니만 못하다. */
export function buildGithubCompareUrl(
  remoteUrl: string,
  fromRevision: string,
  toRevision: string
): string | null {
  const base = repositoryBase(remoteUrl);
  if (!base || !REVISION.test(fromRevision) || !REVISION.test(toRevision)) return null;
  if (fromRevision === toRevision) return null;
  return `${base}/compare/${fromRevision}...${toRevision}`;
}

export function shortRevision(revision: string | null | undefined): string {
  if (!revision) return '알 수 없음';
  return REVISION.test(revision) ? revision.slice(0, 12) : revision;
}
