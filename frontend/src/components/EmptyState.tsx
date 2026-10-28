'use client';

import { cn } from '@/lib/cn';

interface EmptyStateProps {
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
  compact?: boolean;
}

export default function EmptyState({
  title,
  description,
  action,
  className,
  compact = false,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center rounded-lg border border-dashed border-line text-center',
        compact ? 'px-4 py-6' : 'px-6 py-12',
        className,
      )}
    >
      <div className="mb-2.5 flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-panel-soft text-muted">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className="h-4.5 w-4.5">
          <path d="M4 7.5 12 3l8 4.5v9L12 21l-8-4.5v-9Z" strokeLinejoin="round" />
          <path d="M9 12h6" strokeLinecap="round" />
        </svg>
      </div>
      <p className="text-[13px] text-fg">{title}</p>
      {description && <p className="mt-1 max-w-sm text-[11px] leading-relaxed text-muted">{description}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}
