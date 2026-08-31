import type { ReactNode } from 'react';

type StatTone = 'ok' | 'warn' | 'danger' | 'info' | 'neutral';

export type StatStripItem = {
  key?: string;
  label: string;
  value: ReactNode | number | null | undefined;
  unit?: string;
  caption?: ReactNode;
  tone?: StatTone;
  title?: string;
};

type StatStripProps = {
  items: StatStripItem[];
  isLoading?: boolean;
  size?: 'default' | 'lg';
  framed?: boolean;
  ariaLabel?: string;
  className?: string;
};

const toneClass: Record<StatTone, string> = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-danger',
  info: 'bg-info',
  neutral: 'bg-tertiary',
};

function hasValue(value: StatStripItem['value'], isLoading: boolean): boolean {
  return !isLoading && value !== null && value !== undefined;
}

function renderValue(value: StatStripItem['value'], isLoading: boolean): ReactNode {
  if (isLoading || value === null || value === undefined) return '—';
  if (typeof value === 'number') return value.toLocaleString('ko-KR');
  return value;
}

export default function StatStrip({
  items,
  isLoading = false,
  size = 'default',
  framed = false,
  ariaLabel,
  className = '',
}: StatStripProps) {
  const classes = [
    'ops-stat-strip',
    size === 'lg' ? 'ops-stat-strip--large' : '',
    framed ? 'ops-stat-strip--framed' : '',
    className,
  ].filter(Boolean).join(' ');

  return (
    <dl aria-busy={isLoading || undefined} aria-label={ariaLabel} className={classes} data-slot="stat-strip">
      {items.map((item) => {
        const itemLoading = isLoading;
        return (
          <div className="ops-stat" key={item.key ?? item.label} title={item.title}>
            <dt className="ops-stat__label">
              {item.tone ? <span aria-hidden="true" className={`ops-stat__dot ${toneClass[item.tone]}`} /> : null}
              <span>{item.label}</span>
            </dt>
            <dd className="ops-stat__body">
              <span className={`ops-stat__value${itemLoading ? ' is-loading' : ''}${typeof item.value === 'number' ? '' : ' ops-stat__value--text'}`}>
                <span>{renderValue(item.value, itemLoading)}</span>
                {item.unit && hasValue(item.value, itemLoading) ? (
                  <span className="ops-stat__unit">{item.unit}</span>
                ) : null}
              </span>
              {item.caption ? <span className="ops-stat__caption">{item.caption}</span> : null}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
