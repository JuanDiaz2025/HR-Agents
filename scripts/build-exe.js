#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import https from 'node:https';
import { execFileSync } from 'node:child_process';
import { ROOT } from '../src/resources.js';

/**
 * Build standalone executables that embed Node itself, so the app runs on a
 * machine with nothing installed.
 *
 * Node's Single Executable Application support works by injecting a blob into
 * a copy of the node binary. The blob is platform-independent, so one Linux
 * build machine can produce a Windows .exe by injecting into the official
 * Windows node.exe - which is what the win-x64 target below does.
 */

const BUILD = path.join(ROOT, 'build');
const DIST = path.join(ROOT, 'dist');
const NODE_VERSION = process.version;

const ASSETS = [
  'config/scoring-rubric.json',
  'config/personas/larry.json',
  'samples/sample-practice-call.json',
  'src/web/index.html',
  'src/web/app.js',
  'src/web/styles.css',
  '.env.example',
  'README.md',
];

const TARGETS = {
  'win-x64': { file: 'AI-Sales-Practice.exe', url: `https://nodejs.org/dist/${NODE_VERSION}/win-x64/node.exe` },
  'linux-x64': { file: 'AI-Sales-Practice-linux', local: true },
};

function bundle() {
  console.log('1. Bundling the app into a single file...');
  fs.mkdirSync(BUILD, { recursive: true });
  const out = path.join(BUILD, 'bundle.cjs');
  execFileSync(path.join(ROOT, 'node_modules', '.bin', 'esbuild'), [
    path.join(ROOT, 'src', 'main.js'),
    '--bundle',
    '--platform=node',
    `--target=node${process.versions.node.split('.')[0]}`,
    '--format=cjs',
    // ws pulls these in only if they are installed; it works fine without them.
    '--external:bufferutil',
    '--external:utf-8-validate',
    // Unused in packaged mode (ROOT comes from the executable's location), but
    // esbuild needs a value for it to compile the CommonJS bundle.
    '--define:import.meta.url="file:///sea-bundle"',
    `--outfile=${out}`,
  ], { stdio: ['ignore', 'ignore', 'inherit'] });
  console.log(`   ${(fs.statSync(out).size / 1024).toFixed(0)} KB`);
  return out;
}

function buildBlob(bundlePath) {
  console.log('2. Building the SEA blob (app + assets)...');
  const assets = Object.fromEntries(ASSETS.map((rel) => [rel, path.join(ROOT, rel)]));
  for (const [rel, file] of Object.entries(assets)) {
    if (!fs.existsSync(file)) throw new Error(`Asset missing: ${rel}`);
  }

  const configPath = path.join(BUILD, 'sea-config.json');
  fs.writeFileSync(configPath, JSON.stringify({
    main: bundlePath,
    output: path.join(BUILD, 'sea.blob'),
    disableExperimentalSEAWarning: true,
    useSnapshot: false,
    useCodeCache: false,
    assets,
  }, null, 2));

  execFileSync(process.execPath, ['--experimental-sea-config', configPath], { stdio: ['ignore', 'ignore', 'inherit'] });
  const blob = path.join(BUILD, 'sea.blob');
  console.log(`   ${(fs.statSync(blob).size / 1024 / 1024).toFixed(1)} MB`);
  return blob;
}

function download(url, dest) {
  return new Promise((resolve, reject) => {
    const request = (target, redirects = 0) => {
      https.get(target, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          if (redirects > 5) return reject(new Error('Too many redirects'));
          res.resume();
          return request(res.headers.location, redirects + 1);
        }
        if (res.statusCode !== 200) {
          res.resume();
          return reject(new Error(`${target} returned ${res.statusCode}`));
        }
        const file = fs.createWriteStream(dest);
        res.pipe(file);
        file.on('finish', () => file.close(() => resolve(dest)));
        file.on('error', reject);
      }).on('error', reject);
    };
    request(url);
  });
}

async function buildTarget(name, target, blob) {
  const output = path.join(DIST, target.file);
  fs.mkdirSync(DIST, { recursive: true });

  let base;
  if (target.local) {
    base = process.execPath;
  } else {
    base = path.join(BUILD, `node-${name}`);
    if (!fs.existsSync(base)) {
      process.stdout.write(`   downloading node ${NODE_VERSION} for ${name}... `);
      await download(target.url, base);
      console.log(`${(fs.statSync(base).size / 1024 / 1024).toFixed(0)} MB`);
    }
  }

  fs.copyFileSync(base, output);
  fs.chmodSync(output, 0o755);

  execFileSync(path.join(ROOT, 'node_modules', '.bin', 'postject'), [
    output, 'NODE_SEA_BLOB', blob,
    '--sentinel-fuse', 'NODE_SEA_FUSE_fce680ab2cc467b6e072b8b5df1996b2',
  ], { stdio: ['ignore', 'ignore', 'inherit'] });

  console.log(`   ${target.file} - ${(fs.statSync(output).size / 1024 / 1024).toFixed(0)} MB`);
  return output;
}

const only = process.argv[2];
const bundlePath = bundle();
const blob = buildBlob(bundlePath);

console.log('3. Injecting into the Node binary for each platform...');
for (const [name, target] of Object.entries(TARGETS)) {
  if (only && only !== name) continue;
  try {
    await buildTarget(name, target, blob);
  } catch (err) {
    console.error(`   ${name} FAILED: ${err.message}`);
  }
}
console.log(`\nExecutables are in ${DIST}`);
