/** @type {import('next').NextConfig} */
const nextConfig = {
  // Produce a self-contained production server (.next/standalone) so the
  // runtime image ships only the files needed to run, not the full source
  // tree or dev dependencies.
  output: "standalone"
};

export default nextConfig;
