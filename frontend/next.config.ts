import type { NextConfig } from 'next';

const backend = process.env.BACKEND_ORIGIN ?? 'http://127.0.0.1:8010';

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // standalone：产出可独立运行的 server.js，镜像里不必带 node_modules 全量依赖
  output: 'standalone',
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${backend}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
