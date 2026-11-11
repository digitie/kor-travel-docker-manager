import { afterEach, expect, it, vi } from 'vitest';
import { createManagedWebSocket } from './managedWebSocket';
afterEach(() => vi.unstubAllGlobals());
it('종료 시 모든 핸들러를 해제한 뒤 소켓을 닫는다', () => {
  const socket = { onopen: vi.fn(), onmessage: vi.fn(), onerror: vi.fn(), onclose: vi.fn(), close: vi.fn() };
  socket.close.mockImplementation(() => { expect(socket.onopen).toBeNull(); expect(socket.onmessage).toBeNull(); expect(socket.onerror).toBeNull(); expect(socket.onclose).toBeNull(); });
  vi.stubGlobal('WebSocket', vi.fn(function () { return socket; }));
  const connection = createManagedWebSocket('ws://localhost/status');
  expect(connection.socket).toBe(socket); connection.dispose(); expect(socket.close).toHaveBeenCalledOnce();
});
it('생성 실패는 호출자에게 그대로 전달한다', () => {
  vi.stubGlobal('WebSocket', vi.fn(function () { throw new Error('주소 오류'); }));
  expect(() => createManagedWebSocket('invalid')).toThrow('주소 오류');
});
