import type { Metadata } from 'next';
import './globals.css';
import Sidebar from '@/components/Sidebar';

export const metadata: Metadata = {
  title: 'AgentForge 管理控制台',
  description: 'AgentForge 智能体平台：知识库、数据集流水线、工具调试与评测',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-bg text-fg antialiased">
        <Sidebar />
        <main className="ml-[232px] min-h-screen px-6 py-6">{children}</main>
      </body>
    </html>
  );
}
