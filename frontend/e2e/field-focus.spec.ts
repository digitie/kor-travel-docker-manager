import { test, expect, type Locator } from '@playwright/test';
import { login, mockBackend, openMenu } from './mock-backend';

// ui 0.2.5(common #81): 입력·선택·여러 줄의 키보드 포커스는 Button과 같은 outline 하나(2px, offset 2px,
// --kt-focus)뿐이다. 앱 쪽 테두리 색·ring·그림자가 덧대지면 포커스 표시가 둘이 된다(FS-7.8).

type FieldLook = {
  outline: string; outlineOffset: string; outlineColor: string; shadow: string; border: string;
  borderColor: string; parentOutline: string; focusVisible: boolean;
};

async function fieldLook(target: Locator): Promise<FieldLook> {
  return target.evaluate((node) => {
    const style = getComputedStyle(node);
    const parent = node.parentElement ? getComputedStyle(node.parentElement) : null;
    const sides = ['top', 'right', 'bottom', 'left'];
    return {
      outline: style.outlineStyle === 'none' ? 'none' : `${style.outlineStyle} ${style.outlineWidth}`,
      outlineOffset: style.outlineOffset,
      outlineColor: style.outlineColor,
      shadow: style.boxShadow,
      border: sides.map((side) => style.getPropertyValue(`border-${side}-width`)).join(' '),
      borderColor: sides.map((side) => style.getPropertyValue(`border-${side}-color`)).join(' '),
      parentOutline: parent && parent.outlineStyle !== 'none' ? `${parent.outlineStyle} ${parent.outlineWidth}` : 'none',
      focusVisible: node.matches(':focus-visible'),
    };
  });
}

async function settled(target: Locator) {
  await target.page().waitForTimeout(250);
  return fieldLook(target);
}

/** 실제 Tab 이동으로 포커스를 만든다. */
async function tabInto(target: Locator) {
  await target.focus();
  await target.page().keyboard.press('Shift+Tab');
  await target.page().keyboard.press('Tab');
  await expect(target).toBeFocused();
}

async function shot(target: Locator, path: string) {
  const box = (await target.boundingBox())!;
  const pad = 8;
  await target.page().screenshot({ path, clip: { x: Math.max(0, box.x - pad), y: Math.max(0, box.y - pad),
    width: box.width + pad * 2, height: box.height + pad * 2 } });
}

async function expectSingleOutline(name: string, target: Locator, buttonOutlineColor: string, outPath: string) {
  await target.scrollIntoViewIfNeeded();
  await target.page().mouse.move(0, 0);
  const rest = await settled(target);
  await tabInto(target);
  const focus = await settled(target);
  await shot(target, outPath);
  await target.blur();
  const label = `${name}: rest=${JSON.stringify(rest)} focus=${JSON.stringify(focus)}`;
  expect(rest.outline, label).toBe('none');
  expect(focus.focusVisible, label).toBe(true);
  expect(focus.outline, label).toBe('solid 2px');
  expect(focus.outlineOffset, label).toBe('2px');
  // 포커스 색은 버튼과 같은 --kt-focus다.
  expect(focus.outlineColor, label).toBe(buttonOutlineColor);
  // 테두리 두께·색·그림자(ring)는 포커스로 바뀌지 않는다 — 표시는 outline 하나다.
  expect(focus.border, label).toBe(rest.border);
  expect(focus.borderColor, label).toBe(rest.borderColor);
  expect(focus.shadow, label).toBe(rest.shadow);
  expect(focus.parentOutline, label).toBe('none');
}

test('입력·선택·여러 줄 포커스는 2px outline 하나이고 모바일 체크박스 터치 영역은 44px 이상이다', async ({ page }, testInfo) => {
  const mobile = testInfo.project.name === 'mobile';
  const backend = await mockBackend(page, testInfo);
  await login(page);

  // 기준: 버튼의 키보드 포커스 outline 색(--kt-focus).
  const refresh = page.locator('[data-slot="admin-shell-header"]').getByRole('button', { name: '새로고침', exact: true });
  await tabInto(refresh);
  const buttonOutlineColor = (await settled(refresh)).outlineColor;
  await refresh.blur();

  // 모바일(390)에서 "중지 항목 포함" 체크박스는 보이는 크기는 그대로, 누를 수 있는 영역은 44px 이상이다.
  const checkbox = page.getByRole('checkbox', { name: '중지 항목 포함' });
  await checkbox.scrollIntoViewIfNeeded();
  if (mobile) {
    const hit = await checkbox.evaluate((node) => {
      const rect = node.getBoundingClientRect();
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      const after = getComputedStyle(node, '::after');
      // 44×44 정사각형의 네 모서리(1px 안쪽)를 눌러도 체크박스 자신이 맞는다.
      const corners = [[-21, -21], [21, -21], [-21, 21], [21, 21]].map(([dx, dy]) => {
        const element = document.elementFromPoint(cx + dx, cy + dy);
        return element === node || node.contains(element);
      });
      return { visible: [rect.width, rect.height], after: [after.width, after.height], corners };
    });
    const label = JSON.stringify(hit);
    expect(hit.visible[0], label).toBeLessThan(24);
    expect(Number.parseFloat(hit.after[0]), label).toBeGreaterThanOrEqual(44);
    expect(Number.parseFloat(hit.after[1]), label).toBeGreaterThanOrEqual(44);
    expect(hit.corners, label).toEqual([true, true, true, true]);
    await page.screenshot({ path: testInfo.outputPath('checkbox-mobile.png') });
  }

  // 배포 버전 고정: NativeSelect·Input(.ops-input)·Textarea(.ops-input).
  await (await openMenu(page)).getByRole('button', { name: '배포 버전 고정', exact: true }).click();
  await expect(page.getByRole('table', { name: '현재 고정 버전' })).toContainText('cccc');
  const fields: [string, Locator][] = [
    ['pin-role-select', page.locator('#runtime-pin-role')],
    ['pin-revision-input', page.locator('#runtime-pin-revision')],
    ['pin-reason-textarea', page.locator('#runtime-pin-reason')],
  ];
  for (const [name, target] of fields) {
    await expectSingleOutline(name, target, buttonOutlineColor, testInfo.outputPath(`field-${name}-focus.png`));
  }
  await page.keyboard.press('Escape');

  // 컨테이너 설정 변경: 포트 Input(앱 쪽 focus 테두리·ring 덧대기를 지운 곳).
  const table = page.getByRole('table', { name: '서비스 원장' });
  await table.getByTitle('컨테이너 세부 설정 변경').click();
  const config = page.getByRole('dialog', { name: '컨테이너 설정 변경' });
  const port = config.getByLabel('포트 매핑 1', { exact: true });
  await expectSingleOutline('config-port-input', port, buttonOutlineColor, testInfo.outputPath('field-config-port-focus.png'));
  await page.keyboard.press('Escape');
  expect(backend.errors).toEqual([]);
});
