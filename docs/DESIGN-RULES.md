# UI 디자인 규칙

이 문서는 `kor-travel-geo-ui`의 `docs/DESIGN-RULES.md`(StyleSeed 기반)를
`kor-travel-docker-manager` 대시보드에 맞게 반영한 운영 콘솔 규칙이다. StyleSeed는 제품 UI가
"생성된 화면"처럼 보이지 않도록 단일 accent, 의미 토큰, 카드 구조, 낮은 그림자, 일관된 모션을
강조한다. 매니저 대시보드는 이 규칙을 따르며, 기존 BMW M Pure Black 양식([[DESIGN.md]])은
StyleSeed 라이트 토큰 체계로 대체되었다.

## 적용 범위

매니저 대시보드는 내부 운영 콘솔이다. 마케팅식 hero, 장식적 gradient, 큰 CTA보다 스캔하기 쉬운
정보 밀도와 예측 가능한 조작을 우선한다. 빌드 스택은 **Tailwind v4**(CSS-first `@theme`)이며, 토큰은
`frontend/src/app/globals.css`의 `@theme` 블록에 정의한다.

## 핵심 규칙

1. **색상은 단일 accent 중심.** `brand`(teal `#0f766e`)는 active 상태, 진행률 fill, 선택, 작은
   icon/badge, primary 버튼에만 쓴다. 큰 배경면은 `page`/`card`/`subtle`/`elevated` surface 토큰을
   사용하고, 오류/경고/성공 색(`danger`/`warn`/`ok`/`info`)은 작은 badge·dot·text에 제한한다.
2. **텍스트는 5단계 grayscale 토큰.** `strong`(#172033), `ink`(#344054), `secondary`(#667085),
   `tertiary`(#7a8597), `disabled`(#98a2b3). 순수 `#000`과 임의 gray hex는 새로 늘리지 않는다.
3. **카드와 패널은 정보 단위의 경계.** 주요 내용은 `bg-card border border-line rounded-card`
   영역에 둔다. 카드 반경은 8px(`rounded-card`)로 유지한다.
4. **그림자는 아주 약하게.** 기본 카드는 `shadow-card`(4%), floating/modal도 `shadow-modal`(12%)을
   넘기지 않는다. 색이 들어간 그림자는 쓰지 않는다.
5. **조작 대상은 최소 44px touch target.** button, nav link, icon button은 `min-h-[44px]` 또는
   그에 준하는 hit area를 가진다.
6. **label은 작고 일관되게.** 폼 label, table header, group title은 12px, 굵은 weight,
   `tracking-[0.05em]`, uppercase를 기본으로 한다.
7. **상태 표시는 dot과 text를 함께.** 상태 배지는 색만으로 전달하지 않고 같은 색 dot + text를
   함께 보여 준다. 큰 warning/error 배경면은 필요한 안내 박스에만 옅게 사용한다.
8. **숫자는 더 크게, 보조 라벨은 더 작게.** KPI 성격의 metric 값은 크게, label은 12px uppercase로
   둔다. 숫자+단위는 `whitespace-nowrap`으로 줄바꿈을 막는다.
9. **모션은 이름 있는 토큰으로 제한.** hover/focus/press 전환은 `duration-150`/`ease-default`를
   사용한다. `prefers-reduced-motion: reduce`에서는 transition/animation을 사실상 비활성화한다.
10. **새 UI는 semantic token부터.** hardcoded hex 추가 전에 `globals.css`의 `@theme`
    surface/text/brand/status 토큰으로 표현 가능한지 먼저 확인한다.

## 금지

- 순수 검정(`#000`, `text-black`, `bg-black`) 추가
- 브랜드색을 큰 카드 배경, page background, 여러 섹션의 큰 면으로 사용
- 카드 안에 카드를 중첩하거나 page section을 장식용 floating card로 남발
- 보이는 강한 shadow, 색이 들어간 shadow, 컴포넌트마다 다른 shadow 언어
- viewport width에 비례한 font-size 조정

## 토큰 (Tailwind v4 `@theme` → 유틸리티)

| 분류 | 토큰 | 유틸리티 예시 |
|---|---|---|
| surface | `--color-page/card/subtle/elevated/row` | `bg-page`, `bg-card`, `bg-subtle`, `bg-elevated` |
| line | `--color-line` | `border-line` |
| text | `--color-strong/ink/secondary/tertiary/disabled` | `text-strong`, `text-ink`, `text-secondary` |
| brand | `--color-brand/brand-ink/brand-tint` | `text-brand`, `bg-brand`, `bg-brand-tint` |
| status | `--color-info/warn/danger/ok` | `text-ok`, `bg-danger`, `text-warn` |
| shadow | `--shadow-card/card-hover/modal` | `shadow-card`, `shadow-modal` |
| radius | `--radius-card` (8px) | `rounded-card` |
| motion | `--ease-default` | `ease-default`, `duration-150` |

## 현재 코드 적용 지점

- `frontend/src/app/globals.css`: `@theme` 토큰과 base 규칙(배경/텍스트/모션/scrollbar)
- `frontend/src/components/DashboardClient.tsx`: 상태 테이블·메트릭·모달의 surface/text/brand/status 적용
- `frontend/src/components/layout/AppErrorPanel.tsx`: 런타임 오류 복구 패널
