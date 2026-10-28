'use client';

import { cn } from '@/lib/cn';

interface CardProps {
  title?: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
  dense?: boolean;
}

export default function Card({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
  dense = false,
}: CardProps) {
  const hasHeader = Boolean(title || subtitle || actions);
  return (
    <section
      className={cn(
        'rounded-xl border border-line bg-panel shadow-[0_1px_0_rgba(255,255,255,0.02)_inset]',
        className,
      )}
    >
      {hasHeader && (
        <header className="flex items-start justify-between gap-3 border-b border-line-soft px-4 py-3">
          <div className="min-w-0">
            {title && <h2 className="text-[13px] font-medium text-fg">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-[11px] leading-relaxed text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cn(dense ? 'p-3' : 'p-4', bodyClassName)}>{children}</div>
    </section>
  );
}
