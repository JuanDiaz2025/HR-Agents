#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { ensureWorkspace, isPackaged, ROOT } from './resources.js';

/**
 * When packaged, mirror everything to a log file beside the executable.
 *
 * A double-clicked console window that closes on error takes the error with it,
 * which makes a failure on someone else's machine unreportable. The log file
 * survives, so "send me startup-log.txt" always works.
 */
function startLogging() {
  if (!isPackaged) return null;
  let stream;
  try {
    stream = fs.createWriteStream(path.join(ROOT, 'startup-log.txt'), { flags: 'w' });
  } catch {
    return null;
  }

  stream.write(`AI Sales Practice - startup log\n${new Date().toISOString()}\n`);
  stream.write(`platform ${process.platform} ${process.arch} | node ${process.version}\n`);
  stream.write(`executable ${process.execPath}\nworking dir ${ROOT}\n\n`);

  for (const level of ['log', 'warn', 'error']) {
    const original = console[level].bind(console);
    console[level] = (...args) => {
      try {
        stream.write(args.map((a) => (typeof a === 'string' ? a : JSON.stringify(a))).join(' ') + '\n');
      } catch {
        // Logging must never be the thing that breaks startup.
      }
      original(...args);
    };
  }

  const fatal = (label) => (err) => {
    const message = `\n${label}: ${err?.stack || err?.message || String(err)}\n`;
    try { stream.write(message); } catch {}
    process.stderr.write(message);
    process.stderr.write(`\nThis was written to:\n  ${path.join(ROOT, 'startup-log.txt')}\nSend that file and it can be diagnosed.\n`);
    process.stderr.write('\nPress Ctrl+C to close this window.\n');
    setInterval(() => {}, 1 << 30);
  };
  process.on('uncaughtException', fatal('CRASHED'));
  process.on('unhandledRejection', fatal('UNHANDLED PROMISE REJECTION'));

  return stream;
}

/**
 * Node prints an internal warning about require() inside single executables that
 * is irrelevant here - nothing loads modules from disk - but fills a
 * double-clicked console window with alarming text and makes a working app look
 * broken. Replace the default handler so real warnings still surface.
 */
function quietInternalWarnings() {
  const NOISE = [
    /require\(\) provided to the main script/i,
    /single-executable/i,
    /Support for bundled module loading/i,
    /Use `.*--trace-warnings/i,
  ];
  process.removeAllListeners('warning');
  process.on('warning', (warning) => {
    const text = `${warning.name}: ${warning.message}`;
    if (NOISE.some((pattern) => pattern.test(text))) return;
    console.warn(text);
  });
}

quietInternalWarnings();
startLogging();

/**
 * Entry point for the packaged executable.
 *
 * Double-clicked with no arguments it unpacks its files, starts the server and
 * opens the browser - that is the whole interaction. Run from a terminal with
 * arguments it behaves exactly like the CLI.
 */

let created = [];
try {
  ({ created } = ensureWorkspace());
  if (created.length) console.log(`First run - unpacked ${created.length} files into:\n  ${ROOT}\n`);
} catch (err) {
  console.error(`Could not write files next to the app: ${err.message}`);
  console.error(`Move the app to a folder you can write to - Documents works, Program Files does not.\n`);
  throw err;
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
  const url = `http://localhost:${port}`;
  setTimeout(() => {
    const line = '='.repeat(58);
    console.log(`\n${line}`);
    console.log('  THE APP IS RUNNING');
    console.log(`  Open your browser to:  ${url}`);
    console.log('');
    console.log('  This window is not the app - it is just the engine.');
    console.log('  Leave it open. Closing it stops the app.');
    console.log(line + '\n');
    openBrowser(url);
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
