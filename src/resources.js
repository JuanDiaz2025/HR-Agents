import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

/**
 * Where the app's files live, whether it is running from source or as a
 * packaged executable.
 *
 * From source: everything sits in the repo, as normal.
 * Packaged: the config, personas, sample call and web files are baked into the
 * binary, and unpacked once into a folder beside the executable the first time
 * it runs. After that it behaves exactly like the source version - the rubric
 * and personas are ordinary files you can edit, which is the whole point of
 * keeping them as JSON.
 */

// Resolved synchronously against the running binary so this file stays free of
// top-level await - the packaged build is bundled as CommonJS, which cannot
// have any.
let sea = null;
try {
  sea = createRequire(process.execPath)('node:sea');
} catch {
  sea = null;
}

export const isPackaged = Boolean(sea?.isSea?.());

const sourceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/** Beside the .exe when packaged, so the workspace lives where the user put it. */
export const ROOT = isPackaged ? path.dirname(process.execPath) : sourceRoot;

/** Files baked into the executable and unpacked on first run. */
const BUNDLED = [
  'config/scoring-rubric.json',
  'config/personas/larry.json',
  'samples/sample-practice-call.json',
  'src/web/index.html',
  'src/web/app.js',
  'src/web/styles.css',
  '.env.example',
  'README.md',
];

export function ensureWorkspace() {
  if (!isPackaged) return { created: [], root: ROOT };

  const created = [];
  for (const rel of BUNDLED) {
    const target = path.join(ROOT, rel);
    if (fs.existsSync(target)) continue;
    const contents = sea.getAsset(rel, 'utf8');
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, contents);
    created.push(rel);
  }
  return { created, root: ROOT };
}
