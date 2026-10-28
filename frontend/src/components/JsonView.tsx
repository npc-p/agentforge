'use client';

import { useState } from 'react';
import { cn } from '@/lib/cn';
import { toJsonText } from '@/lib/format';

interface JsonViewProps {
  value: unknown;
  /** 折叠状态下展示的最大高度 */
  collapsedHeight?: number;
  defaultOpen?: boolean;
  className?: string;
  emptyText?: string;
}

export default function JsonView({
  value,
  collapsedHeight = 120,
  defaultOpen = false,
  className,
  emptyText = '无数据',
}: JsonViewProps) {
  const [open, setOpen] = useState(defaultOpen);
  const text = toJsonText(value);

  if (text === '') {
    return <div className={cn('text-[11px] text-muted', className)}>{emptyText}</div>;
  }

  return (
    <div className={cn('rounded-lg border border-line bg-[#0d131a]', className)}>
      <div className="flex items-center justify-between border-b border-line-soft px-2.5 py-1.5">
        <span className="font-mono text-[10px] uppercase tracking-widest text-muted">json</span>
        <button
          type="button"
          onClick={() => setOpen((prev) => !prev)}
          className="text-[11px] text-muted transition-colors hover:text-accent"
        >
          {open ? '收起' : '展开'}
        </button>
      </div>
      <pre
        className="overflow-auto px-2.5 py-2 font-mono text-[11px] leading-relaxed text-[#c7d3e1]"
        style={{ maxHeight: open ? 480 : collapsedHeight }}
      >
        {text}
      </pre>
    </div>
  );
}
