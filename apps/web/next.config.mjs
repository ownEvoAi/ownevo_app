/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
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
