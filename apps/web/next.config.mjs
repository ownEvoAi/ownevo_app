/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The REST API URL the server-side fetchers hit. Defaults to the local
  // FastAPI dev server. Override via OWNEVO_KERNEL_API_URL when the kernel
  // is on a different host/port.
  env: {
    OWNEVO_KERNEL_API_URL:
      process.env.OWNEVO_KERNEL_API_URL ?? 'http://localhost:8000',
  },
  // Approval queue is internal; don't index.
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Robots-Tag', value: 'noindex, nofollow' },
        ],
      },
    ]
  },
}

export default nextConfig
