'use client';

import { cn } from '@/lib/cn';

type BannerTone = 'error' | 'info' | 'success' | 'warn';

const TONES: Record<BannerTone, string> = {
  error: 'border-bad/40 bg-bad/10 text-[#ffb3ad]',
  info: 'border-accent/35 bg-accent/10 text-[#b9d7fb]',
  success: 'border-ok/35 bg-ok/10 text-[#a7e6b0]',
  warn: 'border-warn/35 bg-warn/10 text-[#f0d08a]',
};

interface BannerProps {
  tone?: BannerTone;
  children: React.ReactNode;
  onClose?: () => void;
  className?: string;
}

export default function Banner({ tone = 'error', children, onClose, className }: BannerProps) {
  return (
    <div
      className={cn(
        'wb-fade-in flex items-start gap-2 rounded-lg border px-3 py-2 text-[12px] leading-relaxed',
        TONES[tone],
        className,
      )}
      role={tone === 'error' ? 'alert' : 'status'}
    >
      <span className="mt-0.5 shrink-0">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-3.5 w-3.5">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 8v5M12 16.2v.1" strokeLinecap="round" />
        </svg>
      </span>
      <div className="min-w-0 flex-1 break-words">{children}</div>
      {onClose && (
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded px-1 text-[15px] leading-none opacity-70 transition-opacity hover:opacity-100"
          aria-label="关闭提示"
        >
          ×
        </button>
      )}
    </div>
  );
}
