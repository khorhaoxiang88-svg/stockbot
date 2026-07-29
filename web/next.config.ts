import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // better-sqlite3 is a native module. It must stay a real Node require on the
  // server instead of being bundled, or the .node binary cannot be loaded.
  serverExternalPackages: ["better-sqlite3"],
};

export default nextConfig;
