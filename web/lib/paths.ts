import path from "node:path";

/**
 * The web app runs with its working directory at /stockbot/web, so the repo
 * root is one level up. Everything shared (config, data, migrations) is
 * resolved from here.
 */
export const REPO_ROOT = path.resolve(process.cwd(), "..");

function fromRoot(relativeOrAbsolute: string): string {
  return path.isAbsolute(relativeOrAbsolute)
    ? relativeOrAbsolute
    : path.join(REPO_ROOT, relativeOrAbsolute);
}

export const DB_PATH = fromRoot(process.env.STOCKBOT_DB ?? "data/stockbot.db");

export const CONFIG_PATH = fromRoot(
  process.env.STOCKBOT_CONFIG ?? "config.frozen.json",
);
