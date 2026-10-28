'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/cn';

interface NavItem {
  href: string;
  label: string;
  desc: string;
  icon: React.ReactNode;
}

const NAV: NavItem[] = [
  {
    href: '/',
    label: '概览',
    desc: '系统健康与统计',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" className="h-4 w-4">
        <rect x="3" y="3" width="7" height="7" rx="1.5" />
        <rect x="14" y="3" width="7" height="7" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" />
        <rect x="14" y="14" width="7" height="7" rx="1.5" />
      </svg>
    ),
  },
  {
    href: '/chat',
    label: '对话',
    desc: '智能体实时推理',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" className="h-4 w-4">
        <path d="M21 12a8 8 0 0 1-8 8H8l-5 3 1.6-4.3A8 8 0 1 1 21 12Z" strokeLinejoin="round" />
        <circle cx="9" cy="12" r="1" fill="currentColor" stroke="none" />
        <circle cx="12.5" cy="12" r="1" fill="currentColor" stroke="none" />
        <circle cx="16" cy="12" r="1" fill="currentColor" stroke="none" />
      </svg>
    ),
  },
  {
    href: '/knowledge',
    label: '知识库',
    desc: '文档与检索调试',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" className="h-4 w-4">
        <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v18H6.5A2.5 2.5 0 0 0 4 18.5Z" strokeLinejoin="round" />
        <path d="M8 7h7M8 11h7" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    href: '/dataset',
    label: '数据集',
    desc: '清洗与质检流水线',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" className="h-4 w-4">
        <ellipse cx="12" cy="6" rx="7" ry="3" />
        <path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6" />
        <path d="M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />
      </svg>
    ),
  },
  {
    href: '/tools',
    label: '工具',
    desc: '注册表与在线调试',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" className="h-4 w-4">
        <path
          d="M14.5 6.5a3.5 3.5 0 1 0 4.2 4.2l-8 8a2.6 2.6 0 0 1-3.7-3.7l8-8Z"
          strokeLinejoin="round"
        />
        <path d="M5 19l1-1" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    href: '/eval',
    label: '评测',
    desc: '检索与智能体指标',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" className="h-4 w-4">
        <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" strokeLinecap="round" />
      </svg>
    ),
  },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed inset-y-0 left-0 z-20 flex w-[232px] flex-col border-r border-line bg-panel">
      <div className="flex items-center gap-2.5 px-5 py-5">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent/15 text-accent">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-5 w-5">
            <path d="M12 3 4 7.5v9L12 21l8-4.5v-9L12 3Z" strokeLinejoin="round" />
            <path d="M12 12v9M12 12 4 7.5M12 12l8-4.5" strokeLinejoin="round" />
          </svg>
        </div>
        <div className="leading-tight">
          <div className="text-[15px] font-semibold tracking-wide">AgentForge</div>
          <div className="text-[11px] text-muted">管理控制台</div>
        </div>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto px-2.5 py-2">
        {NAV.map((item) => {
          const active = item.href === '/' ? pathname === '/' : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'group flex items-start gap-2.5 rounded-lg px-2.5 py-2 transition-colors',
                active
                  ? 'bg-accent/12 text-fg'
                  : 'text-muted hover:bg-panel-soft hover:text-fg',
              )}
            >
              <span
                className={cn(
                  'mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border',
                  active
                    ? 'border-accent/40 bg-accent/15 text-accent'
                    : 'border-line bg-panel-soft text-muted group-hover:text-fg',
                )}
              >
                {item.icon}
              </span>
              <span className="min-w-0">
                <span className={cn('block text-[13px]', active && 'font-medium')}>{item.label}</span>
                <span className="block truncate text-[11px] text-muted/80">{item.desc}</span>
              </span>
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-line px-4 py-3 text-[11px] leading-relaxed text-muted">
        <div className="flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-muted" />
          后端 API {process.env.NEXT_PUBLIC_API_LABEL ?? '127.0.0.1:8010'}
        </div>
        <div className="mt-1 text-muted/70">AgentForge Console v0.1</div>
      </div>
    </aside>
  );
}
