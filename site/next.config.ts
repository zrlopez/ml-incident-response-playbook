import path from 'node:path';
import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  // Keep resolution hermetic to this package even if a parent workspace exists.
  turbopack: {
    root: path.join(__dirname),
  },
};

export default nextConfig;
