#!/usr/bin/env node

import { Command } from 'commander';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import { readConfig, writeConfig } from './config.js';
import { readSession, resetSessionForProfile } from './session.js';
import { VocaDaemon, PID_FILE, STATE_FILE, ASSISTANT_DIR } from './daemon.js';

const require = createRequire(import.meta.url);
const pkg = require('../package.json');

const VALID_PROFILES = ['personal', 'public'];

export const program = new Command();

program
  .name('voca')
  .version(pkg.version)
  .description('Voice Operated Claw Assistant');

const session = program.command('session').description('Session management');

session
  .command('new')
  .description('Create a new session for the current profile')
  .action(async () => {
    const config = await readConfig();
    const s = await resetSessionForProfile(config.profile);
    console.log(`New session: ${s.sessionId} (profile: ${s.profile})`);
  });

session
  .command('info')
  .description('Show current session info')
  .action(async () => {
    const s = await readSession();
    console.log(`Session:  ${s.sessionId}`);
    console.log(`Profile:  ${s.profile}`);
    console.log(`Messages: ${s.messageCount}`);
    console.log(`Created:  ${s.createdAt}`);
  });

const profile = program.command('profile').description('Profile management');

profile
  .command('list')
  .description('List available profiles')
  .action(() => {
    for (const p of VALID_PROFILES) {
      console.log(p);
    }
  });

profile
  .command('use <id>')
  .description('Switch to a profile')
  .action(async (id: string) => {
    if (!VALID_PROFILES.includes(id)) {
      console.error(`Unknown profile: ${id}. Valid profiles: ${VALID_PROFILES.join(', ')}`);
      process.exitCode = 1;
      return;
    }
    const config = await readConfig();
    config.profile = id;
    await writeConfig(config);
    const s = await resetSessionForProfile(id);
    console.log(`Switched to profile: ${id}`);
    console.log(`New session: ${s.sessionId}`);
  });

program
  .command('start')
  .description('Start the voice assistant daemon')
  .option('--daemon', 'Run as a background daemon')
  .action(async (opts: { daemon?: boolean }) => {
    if (opts.daemon) {
      await fs.mkdir(ASSISTANT_DIR, { recursive: true });
      const child = spawn(
        process.execPath,
        [...process.execArgv, fileURLToPath(import.meta.url), 'start'],
        { detached: true, stdio: 'ignore' },
      );
      child.unref();
      await fs.writeFile(PID_FILE, String(child.pid) + '\n');
      console.log(`Daemon started (PID: ${child.pid})`);
      process.exit(0);
      return;
    }

    const config = await readConfig();
    const daemon = new VocaDaemon(config);

    const shutdown = async () => {
      console.log('\nShutting down…');
      await daemon.stop();
      await daemon.cleanup();
      process.exit(0);
    };

    process.on('SIGINT', shutdown);
    process.on('SIGTERM', shutdown);

    await daemon.start();
  });

program
  .command('stop')
  .description('Stop the voice assistant daemon')
  .action(async () => {
    let pidStr: string;
    try {
      pidStr = await fs.readFile(PID_FILE, 'utf-8');
    } catch {
      console.log('Daemon not running');
      return;
    }
    const pid = Number(pidStr.trim());
    try {
      process.kill(pid, 'SIGTERM');
    } catch {
      // process already gone
    }
    // Give the daemon a moment to clean up
    await new Promise((r) => setTimeout(r, 500));
    try { await fs.unlink(PID_FILE); } catch { /* already removed */ }
    console.log('Daemon stopped');
  });

program
  .command('status')
  .description('Show daemon status')
  .action(async () => {
    let data: string;
    try {
      data = await fs.readFile(STATE_FILE, 'utf-8');
    } catch {
      console.log('Daemon not running');
      return;
    }
    const state = JSON.parse(data);
    console.log(`State:     ${state.state}`);
    console.log(`Session:   ${state.sessionId}`);
    console.log(`Profile:   ${state.profile}`);
    console.log(`Updated:   ${state.updatedAt}`);
  });

program
  .command('bootstrap')
  .description('Interactive setup for mic/speaker/dependencies')
  .action(async () => {
    const { runBootstrap } = await import('./bootstrap.js');
    await runBootstrap();
  });

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  program.parse();
}
