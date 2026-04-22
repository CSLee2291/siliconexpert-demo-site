/** @type {import('next').NextConfig} */
const config = {
  async rewrites() {
    return [{ source: '/', destination: '/index.html' }];
  },
};

export default config;
