'use client';

import { cn } from '@/lib/cn';

interface StatCardProps {
  label: string;
  value: string | number;
  hint?: string;
  accent?: 'default' | 'accent' | 'ok' | 'warn' | 'bad';
  className?: string;
}

const ACCENTS: Record<NonNullable<StatCardProps['accent']>, string> = {
  default: 'text-fg',
  accent: 'text-accent',
  ok: 'text-ok',
  warn: 'text-warn',
  bad: 'text-bad',
};

export default function StatCard({ label, value, hint, accent = 'default', className }: StatCardProps) {
  return (
    <div
      className={cn(
        'rounded-xl border border-line bg-panel px-4 py-3.5 transition-colors hover:border-line/80 hover:bg-panel-soft/40',
        className,
      )}
    >
      <div className="text-[11px] tracking-wide text-muted">{label}</div>
      <div className={cn('mt-1.5 font-mono text-[22px] font-semibold leading-none', ACCENTS[accent])}>
        {value}
      </div>
      {hint && <div className="mt-1.5 truncate text-[11px] text-muted/80">{hint}</div>}
    </div>
  );
}
