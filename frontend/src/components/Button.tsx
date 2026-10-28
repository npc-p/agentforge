'use client';

import { cn } from '@/lib/cn';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md';

const VARIANTS: Record<Variant, string> = {
  primary:
    'bg-accent text-[#04121f] hover:bg-[#63a9fb] border border-accent/60 shadow-[0_0_0_1px_rgba(79,156,249,0.15)]',
  secondary: 'border border-line bg-panel-soft text-fg hover:border-accent/40 hover:text-accent',
  ghost: 'border border-transparent text-muted hover:bg-panel-soft hover:text-fg',
  danger: 'border border-bad/40 bg-bad/12 text-bad hover:bg-bad/20',
};

const SIZES: Record<Size, string> = {
  sm: 'h-7 px-2.5 text-[12px] rounded-md gap-1.5',
  md: 'h-9 px-3.5 text-[13px] rounded-lg gap-2',
};

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

export default function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  disabled,
  className,
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      className={cn(
        'inline-flex select-none items-center justify-center whitespace-nowrap font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-50',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {loading && (
        <span className="h-3 w-3 animate-spin rounded-full border-[1.5px] border-current border-t-transparent" />
      )}
      {children}
    </button>
  );
}
