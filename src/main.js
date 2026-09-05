#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { ensureWorkspace, isPackaged, ROOT } from './resources.js';

/**
 * Entry point for the packaged executable.
 *
 * Double-clicked with no arguments it unpacks its files, starts the server and
 * opens the browser - that is the whole interaction. Run from a terminal with
 * arguments it behaves exactly like the CLI.
 */

const { created } = ensureWorkspace();
if (created.length) {
  console.log(`First run - unpacked ${created.length} files into:\n  ${ROOT}\n`);
}

const args = process.argv.slice(2);
const isDoubleClick = isPackaged && args.length === 0;

if (!args.length) process.argv.push('serve');

function openBrowser(url) {
  const command = process.platform === 'win32' ? 'cmd' : process.platform === 'darwin' ? 'open' : 'xdg-open';
  const argv = process.platform === 'win32' ? ['/c', 'start', '', url] : [url];
  try {
    const child = spawn(command, argv, { detached: true, stdio: 'ignore' });
    // spawn reports a missing opener asynchronously, and an unhandled 'error'
    // event takes the whole process down. Opening a browser is a convenience;
    // the URL is printed either way.
    child.on('error', () => {});
    child.unref();
  } catch {
    // Same reasoning for the synchronous failure path.
  }
}

if (isDoubleClick) {
  const port = Number(process.env.PORT || 3000);
  setTimeout(() => {
    console.log(`\nOpening http://localhost:${port} in your browser.`);
    console.log('Leave this window open while you use the app. Close it to stop.\n');
    openBrowser(`http://localhost:${port}`);
  }, 800);
}

import('./cli.js').catch((err) => {
  console.error(`\nFailed to start: ${err.message}\n`);
  if (isDoubleClick) {
    console.error('Press Ctrl+C to close this window.');
    setInterval(() => {}, 1 << 30);
  } else {
    process.exitCode = 1;
  }
});
