import { test, expect } from '@playwright/test';
import { login, mockBackend, openMenu } from './mock-backend';

// 운영 Docker·인증 서버를 호출하지 않고 common UI의 실제 브라우저 계약을 검증한다.
test('공통 로그인·표·확인·모달·설정 행과 로그아웃', async ({ page }, testInfo) => {
  const backend = await mockBackend(page, testInfo);
  const actions = backend.actions;
  await login(page);
  const table = page.getByRole('table', { name: '서비스 원장' });
  await expect(table).toContainText('demo-service');
  // 펼친 256px rail 옆 1024px 화면에서 표의 최소폭이 스크롤 영역을 늘리지 않는다.
  const originalViewport = page.viewportSize()!;
  await page.setViewportSize({ width: 1024, height: 1000 });
  await expect.poll(() => table.evaluate((element) => {
    const scrollport = element.closest<HTMLElement>('[data-slot="table-container"]')!;
    const wrapper = element.closest<HTMLElement>('.ops-table-wrap')!;
    const bounds = scrollport.getBoundingClientRect();
    const outer = wrapper.getBoundingClientRect();
    return bounds.left >= outer.left - 1 && bounds.right <= outer.right + 1
      && wrapper.scrollWidth <= wrapper.clientWidth + 1;
  })).toBe(true);
  const lastColumnReachable = await table.evaluate((element) => {
    const scrollport = element.closest<HTMLElement>('[data-slot="table-container"]')!;
    scrollport.scrollLeft = scrollport.scrollWidth;
    const bounds = scrollport.getBoundingClientRect();
    const last = element.querySelector('thead th:last-child')!.getBoundingClientRect();
    const reachable = last.left >= bounds.left - 1 && last.right <= bounds.right + 1;
    scrollport.scrollLeft = 0;
    return reachable;
  });
  expect(lastColumnReachable).toBe(true);
  await page.setViewportSize(originalViewport);
  if (testInfo.project.name === 'mobile') {
    // 좁은 폭에서는 rail 대신 한 줄 상단 바의 메뉴 버튼만 남는다.
    await expect(page.locator('[data-slot="admin-shell-rail"]')).toBeHidden();
    const header = await page.locator('[data-slot="admin-shell-header"]').boundingBox();
    expect(header?.height).toBeLessThan(64);
  }
  const stop = table.getByRole('button', { name: '중지', exact: true });
  await stop.click(); await expect(page.getByRole('alertdialog')).toContainText('검증 서비스');
  expect(actions()).toBe(0); await page.getByRole('button', { name: '취소', exact: true }).click();
  await expect(stop).toBeFocused(); expect(actions()).toBe(0);
  await stop.click(); await page.getByRole('button', { name: '계속', exact: true }).click();
  await expect.poll(actions).toBe(1);
  await table.getByTitle('inspect 상세(상태·리소스·mounts·networks·healthcheck·env) 보기').click();
  const detail = page.getByRole('dialog', { name: /상세 정보/ });
  await expect(detail).toBeVisible(); await detail.getByRole('tab', { name: '마운트' }).click();
  await expect(detail.getByRole('table', { name: '마운트' })).toContainText('/data');
  await page.keyboard.press('Escape'); await expect(detail).toHaveCount(0);
  await table.getByTitle('컨테이너 세부 설정 변경').click();
  const config = page.getByRole('dialog', { name: '컨테이너 설정 변경' });
  const port = config.getByLabel('포트 매핑 2', { exact: true });
  const retainedPort = await port.elementHandle();
  await port.fill('12992:9002'); await expect(port).toBeFocused();
  expect(await retainedPort?.evaluate((element) => element.isConnected)).toBe(true);
  await config.getByLabel('포트 매핑 1 삭제', { exact: true }).click();
  await expect(config.getByLabel('포트 매핑 1', { exact: true })).toHaveValue('12992:9002');
  expect(await retainedPort?.evaluate((element) => element.isConnected)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('common-ui.png'), fullPage: false });
  await page.keyboard.press('Escape'); await expect(config).toHaveCount(0);
  await (await openMenu(page)).getByRole('button', { name: '백업 이력', exact: true }).click();
  await expect(page.getByRole('table', { name: '백업 이력' })).toContainText('demo-backup.dump');
  await page.keyboard.press('Escape');
  await (await openMenu(page)).getByRole('button', { name: '배포 버전 고정', exact: true }).click();
  await expect(page.getByRole('table', { name: '현재 고정 버전' })).toContainText('cccc');
  await page.keyboard.press('Escape');
  await expect(page.locator('body')).toHaveCSS('font-family', /Pretendard/);
  await page.getByRole('button', { name: '빠른 명령', exact: true }).first().click();
  const command = page.getByRole('dialog', { name: '빠른 명령' });
  const search = command.getByLabel('명령 검색');
  await search.fill('로그아웃'); await expect(search).toBeFocused();
  await command.getByRole('button', { name: /로그아웃/ }).click();
  await expect(page.getByRole('button', { name: '로그인', exact: true })).toBeVisible();
  expect(backend.errors).toEqual([]);
});
