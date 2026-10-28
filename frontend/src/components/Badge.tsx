'use client';

import { cn } from '@/lib/cn';

export type BadgeTone = 'ok' | 'bad' | 'warn' | 'info' | 'neutral';

const TONES: Record<BadgeTone, string> = {
  ok: 'border-ok/35 bg-ok/12 text-ok',
  bad: 'border-bad/35 bg-bad/12 text-bad',
  warn: 'border-warn/35 bg-warn/12 text-warn',
  info: 'border-accent/35 bg-accent/12 text-accent',
  neutral: 'border-line bg-panel-soft text-muted',
};

interface BadgeProps {
  tone?: BadgeTone;
  children: React.ReactNode;
  className?: string;
  dot?: boolean;
}

export default function Badge({ tone = 'neutral', children, className, dot = false }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-4',
        TONES[tone],
        className,
      )}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

/** 把后端状态字符串映射成徽章色调。 */
export function statusTone(status: string | null | undefined): BadgeTone {
  const value = (status ?? '').toLowerCase();
  if (['ok', 'success', 'succeeded', 'passed', 'ready', 'active', 'completed', 'done', 'healthy'].includes(value)) {
    return 'ok';
  }
  if (['failed', 'error', 'rejected', 'fail', 'unhealthy'].includes(value)) return 'bad';
  if (['running', 'pending', 'processing', 'queued', 'degraded', 'parsing'].includes(value)) return 'warn';
  if (['skipped', 'dropped', 'duplicate'].includes(value)) return 'neutral';
  return value ? 'info' : 'neutral';
}
