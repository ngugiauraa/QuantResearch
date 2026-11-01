/** Static export: FastAPI serves these files, so one local process runs the whole dashboard. */
const config = {
  output: "export",
  reactStrictMode: true,
  images: { unoptimized: true },
  async rewrites() {
    return [];
  },
};
if (process.env.NODE_ENV === "production") delete config.rewrites;
export default config;
