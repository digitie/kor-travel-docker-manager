import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { useConfirm } from './useConfirm';

function Harness({ run, enabled = true }: { run: (accepted: boolean) => void; enabled?: boolean }) {
  const { confirm, confirmation } = useConfirm(enabled);
  return <><button onClick={async () => run(await confirm('대상 두 개를 중지합니다.'))}>중지 요청</button>{confirmation}</>;
}
afterEach(cleanup);
it('승인 전에는 실행하지 않고 취소 후 호출 버튼으로 돌아간다', async () => {
  const user = userEvent.setup(); const run = vi.fn(); render(<Harness run={run} />);
  const trigger = screen.getByRole('button', { name: '중지 요청' }); await user.click(trigger);
  expect(await screen.findByRole('alertdialog')).toHaveTextContent('대상 두 개');
  expect(run).not.toHaveBeenCalled(); await user.click(screen.getByRole('button', { name: '취소' }));
  expect(run).toHaveBeenCalledExactlyOnceWith(false); await waitFor(() => expect(trigger).toHaveFocus());
});
it('계속을 눌렀을 때만 한 번 승인한다', async () => {
  const user = userEvent.setup(); const run = vi.fn(); render(<Harness run={run} />);
  await user.click(screen.getByRole('button', { name: '중지 요청' }));
  await user.click(await screen.findByRole('button', { name: '계속' }));
  expect(run).toHaveBeenCalledExactlyOnceWith(true);
});

it('인증 관측이 중단되면 확인 대기를 취소하고 재로그인 후 새 확인을 허용한다', async () => {
  const user = userEvent.setup(); const run = vi.fn();
  const view = render(<Harness run={run} />);
  await user.click(screen.getByRole('button', { name: '중지 요청' }));
  expect(await screen.findByRole('alertdialog')).toBeVisible();
  view.rerender(<Harness run={run} enabled={false} />);
  await waitFor(() => expect(run).toHaveBeenCalledExactlyOnceWith(false));
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  view.rerender(<Harness run={run} />);
  await user.click(screen.getByRole('button', { name: '중지 요청' }));
  await user.click(await screen.findByRole('button', { name: '계속' }));
  expect(run).toHaveBeenLastCalledWith(true); expect(run).toHaveBeenCalledTimes(2);
});
