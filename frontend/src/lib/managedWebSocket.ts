/** 연결과 정리를 하나의 소유권으로 반환한다. 생성 예외는 기존 호출자가 처리한다. */
export function createManagedWebSocket(url: string) {
  const socket = new WebSocket(url);
  return {
    socket,
    dispose() {
      // 종료 콜백이 새 연결/인증 상태를 오염시키지 않도록 close 전에 해제한다.
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      socket.close();
    },
  };
}
