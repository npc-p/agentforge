'use client';

import { cn } from '@/lib/cn';

interface LoadingDotsProps {
  label?: string;
  className?: string;
}

export default function LoadingDots({ label = '加载中', className }: LoadingDotsProps) {
  return (
    <div className={cn('flex items-center gap-2 text-[12px] text-muted', className)}>
      <span className="flex items-center gap-1">
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className="h-1.5 w-1.5 rounded-full bg-accent"
            style={{ animation: 'wb-blink 1.1s ease-in-out infinite', animationDelay: `${index * 0.16}s` }}
          />
        ))}
      </span>
      {label}
    </div>
  );
}
