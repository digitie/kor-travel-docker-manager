import { test, expect, type Locator, type Page } from '@playwright/test';
import { login, mockBackend, openMenu } from './mock-backend';

const STORAGE_KEY = 'kor-travel-docker-manager:sidebar-collapsed';

async function noHorizontalScroll(page: Page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
}

test('관리자 셸: 한 줄 "영역 - 페이지" 상단 바와 좌측 rail·drawer', async ({ page }, testInfo) => {
  // 기존 Manager rail이 남긴 "0" 저장값을 그대로 읽고 같은 형식으로 갱신해야 한다.
  await page.addInitScript((key) => { if (!sessionStorage.getItem('seeded')) {
    localStorage.setItem(key, '0'); sessionStorage.setItem('seeded', '1'); } }, STORAGE_KEY);
  const backend = await mockBackend(page, testInfo);
  await login(page);

  const header = page.locator('[data-slot="admin-shell-header"]');
  const heading = header.getByRole('heading', { level: 1 });
  await expect(heading).toHaveText('Docker Manager - 대시보드');
  await expect(page).toHaveTitle('Docker Manager - 대시보드');
  // 페이지 설명은 상단 바 안에 작게, 앱 설명(별도 부제·eyebrow)은 없다.
  const description = header.locator('[data-slot="admin-shell-description"]');
  await expect(description).toHaveText('Docker 컨테이너 상태와 조작을 한 화면에서 확인합니다.');
  expect(Number.parseFloat(await description.evaluate((node) => getComputedStyle(node).fontSize)))
    .toBeLessThan(Number.parseFloat(await heading.evaluate((node) => getComputedStyle(node).fontSize)));
  await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
  const headerBox = (await header.boundingBox())!;
  expect(headerBox.height).toBeLessThanOrEqual(56);
  // 제목·설명·동작이 한 줄에 있다(세로로 쌓이지 않는다).
  for (const part of [heading, header.getByRole('button', { name: '새로고침' })]) {
    const box = (await part.boundingBox())!;
    expect(box.y).toBeGreaterThanOrEqual(headerBox.y - 1);
    expect(box.y + box.height).toBeLessThanOrEqual(headerBox.y + headerBox.height + 1);
  }
  expect(await noHorizontalScroll(page)).toBe(true);

  const rail = page.locator('[data-slot="admin-shell-rail"]');
  const menuButton = page.locator('[data-slot="admin-shell-menu-button"]');
  if (testInfo.project.name === 'mobile') {
    await expect(rail).toBeHidden();
    await expect(menuButton).toBeVisible();
    // 상단 바 동작은 아이콘 버튼만 남고 접근성 이름은 유지한다.
    for (const name of ['새로고침', '빠른 명령']) {
      const action = header.getByRole('button', { name, exact: true });
      await expect(action).toBeVisible();
      await expect(action.locator('.ops-button__label')).toBeHidden();
    }
    await page.screenshot({ path: testInfo.outputPath('shell-mobile-closed.png') });

    const drawer = await openMenu(page);
    const box = (await drawer.boundingBox())!;
    expect(box.x).toBeLessThanOrEqual(1);
    expect(box.width).toBeLessThanOrEqual(280);
    await expect(drawer.getByRole('link', { name: '대시보드' })).toHaveAttribute('aria-current', 'page');
    await expect(drawer.getByRole('button', { name: '로그아웃' })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('shell-mobile-drawer.png') });

    // 같은 문서 앵커로 이동하면 drawer가 닫힌다.
    await drawer.getByRole('link', { name: '서비스 원장' }).click();
    await expect(drawer).toBeHidden();
    await expect(page).toHaveURL(/#service-ledger$/);

    // 동작 메뉴는 drawer를 닫고 해당 대화상자로 포커스를 넘긴다.
    const again = await openMenu(page);
    await again.getByRole('button', { name: '백업 이력', exact: true }).click();
    await expect(again).toBeHidden();
    const backups = page.getByRole('dialog', { name: /백업 이력/ });
    await expect(backups).toBeVisible();
    await expect.poll(() => backups.evaluate((node) => node.contains(document.activeElement))).toBe(true);
    await page.keyboard.press('Escape');
    await expect(backups).toHaveCount(0);
  } else {
    await expect(menuButton).toBeHidden();
    await expect(rail).toBeVisible();
    expect((await rail.boundingBox())!.width).toBeCloseTo(256, 0);
    await expect(rail.getByRole('link', { name: '대시보드' })).toHaveAttribute('aria-current', 'page');
    await page.screenshot({ path: testInfo.outputPath('shell-desktop.png') });

    await rail.getByRole('button', { name: '메뉴 접기' }).click();
    await expect.poll(async () => (await rail.boundingBox())!.width).toBeCloseTo(64, 0);
    expect(await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY)).toBe('1');
    // 접힌 rail에서도 메뉴와 로그아웃은 아이콘 버튼으로 남고 이름은 유지한다.
    await expect(rail.getByRole('button', { name: '백업 이력' })).toBeVisible();
    await expect(rail.getByRole('button', { name: '로그아웃' })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('shell-desktop-collapsed.png') });
    await page.reload();
    await expect(page.getByRole('table', { name: '서비스 원장' })).toContainText('demo-service');
    await expect.poll(async () => (await rail.boundingBox())!.width).toBeCloseTo(64, 0);
    await rail.getByRole('button', { name: '메뉴 펼치기' }).click();
    expect(await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY)).toBe('0');
  }
  expect(backend.errors).toEqual([]);
});

type Look = {
  border: string; outline: string; outlineOffset: string; shadow: string; top: number; height: number; focusVisible: boolean;
};

async function look(target: Locator): Promise<Look> {
  return target.evaluate((node) => {
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return {
      border: ['Top', 'Right', 'Bottom', 'Left'].map((side) => style.getPropertyValue(`border-${side.toLowerCase()}-width`)).join(' '),
      outline: style.outlineStyle === 'none' ? 'none' : `${style.outlineStyle} ${style.outlineWidth}`,
      outlineOffset: style.outlineOffset,
      shadow: style.boxShadow,
      top: rect.top,
      height: rect.height,
      focusVisible: node.matches(':focus-visible'),
    };
  });
}

/** 전이(transition)가 끝난 뒤의 계산값을 읽는다. */
async function settled(target: Locator) {
  await target.page().waitForTimeout(250);
  return look(target);
}

async function shot(target: Locator, path: string) {
  const box = (await target.boundingBox())!;
  const pad = 8;
  await target.page().screenshot({ path, clip: { x: Math.max(0, box.x - pad), y: Math.max(0, box.y - pad),
    width: box.width + pad * 2, height: box.height + pad * 2 } });
}

test('버튼 변형별 Tab 포커스·hover·active·disabled에서 테두리가 굵어지지 않는다', async ({ page }, testInfo) => {
  const mobile = testInfo.project.name === 'mobile';
  if (!mobile) await page.setViewportSize({ width: 1280, height: 900 });
  const backend = await mockBackend(page, testInfo);
  await login(page);
  const header = page.locator('[data-slot="admin-shell-header"]');
  const table = page.getByRole('table', { name: '서비스 원장' });
  const variants: [string, Locator][] = [
    ['topbar-refresh', header.getByRole('button', { name: '새로고침', exact: true })],
    ['topbar-primary', header.getByRole('button', { name: '빠른 명령', exact: true })],
    ['row-danger', table.getByRole('button', { name: '중지', exact: true })],
    ['row-icon', table.getByTitle('컨테이너 재부팅')],
    ['row-metric', table.getByTitle('지난 1시간 CPU 사용 이력 보기')],
    [mobile ? 'shell-menu' : 'shell-collapse', mobile ? page.locator('[data-slot="admin-shell-menu-button"]')
      : page.locator('[data-slot="admin-shell-collapse"]')],
  ];
  if (!mobile) {
    const rail = page.locator('[data-slot="admin-shell-rail"]');
    variants.push(['menu-action', rail.getByRole('button', { name: '백업 이력', exact: true })]);
    variants.push(['rail-ghost', rail.getByRole('button', { name: '로그아웃', exact: true })]);
  }
  for (const [name, target] of variants) {
    await target.scrollIntoViewIfNeeded();
    await page.mouse.move(0, 0);
    const rest = await settled(target);
    await shot(target, testInfo.outputPath(`button-${name}-rest.png`));

    await target.hover();
    const hover = await settled(target);
    await shot(target, testInfo.outputPath(`button-${name}-hover.png`));

    await page.mouse.down();
    const active = await settled(target);
    await shot(target, testInfo.outputPath(`button-${name}-active.png`));
    // 버튼 밖에서 놓아 click이 일어나지 않게 한다.
    await page.mouse.move(0, 0);
    await page.mouse.up();

    // 실제 Tab 이동으로 :focus-visible을 만든다.
    await target.focus();
    await page.keyboard.press('Shift+Tab');
    await page.keyboard.press('Tab');
    await expect(target).toBeFocused();
    const focus = await settled(target);
    await shot(target, testInfo.outputPath(`button-${name}-focus.png`));
    await target.blur();

    const label = `${name}: rest=${JSON.stringify(rest)} hover=${JSON.stringify(hover)} active=${JSON.stringify(active)} focus=${JSON.stringify(focus)}`;
    // 상태가 바뀌어도 테두리 두께·그림자는 그대로이고 outline은 키보드 포커스에서만 하나(2px, offset 2px)다.
    for (const state of [hover, active, focus]) {
      expect(state.border, label).toBe(rest.border);
      expect(state.shadow, label).toBe(rest.shadow);
      expect(state.height, label).toBeCloseTo(rest.height, 1);
    }
    expect(rest.outline, label).toBe('none');
    expect(hover.outline, label).toBe('none');
    expect(active.outline, label).toBe('none');
    expect(focus.focusVisible, label).toBe(true);
    expect(focus.outline, label).toBe('solid 2px');
    expect(focus.outlineOffset, label).toBe('2px');
    // 누름 상태의 이동은 1px 이하다(이중 translate 금지).
    expect(Math.abs(active.top - rest.top), label).toBeLessThanOrEqual(1.01);
  }

  // 비활성 버튼은 hover해도 테두리·outline이 변하지 않는다.
  const disabled = table.getByRole('button', { name: '시작', exact: true });
  await expect(disabled).toBeDisabled();
  await page.mouse.move(0, 0);
  const disabledRest = await settled(disabled);
  await disabled.hover({ force: true });
  const disabledHover = await settled(disabled);
  await shot(disabled, testInfo.outputPath('button-row-disabled.png'));
  expect(disabledHover.border).toBe(disabledRest.border);
  expect(disabledHover.outline).toBe('none');
  expect(disabledHover.shadow).toBe(disabledRest.shadow);
  expect(backend.errors).toEqual([]);
});
