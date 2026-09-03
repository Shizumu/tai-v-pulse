import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = dirname(fileURLToPath(import.meta.url));
const vinextCli = join(projectRoot, "node_modules", "vinext", "dist", "cli.js");
const args = process.argv.slice(2);

if (!args.length) {
  console.error("請指定 vinext 指令，例如 dev、build 或 start。");
  process.exit(2);
}

const result = spawnSync(process.execPath, [vinextCli, ...args], {
  cwd: projectRoot,
  env: {
    ...process.env,
    WRANGLER_LOG_PATH: ".wrangler/wrangler.log",
  },
  stdio: "inherit",
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}

process.exit(result.status ?? 1);
