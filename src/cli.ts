#!/usr/bin/env node

import { Command } from 'commander';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { readConfig, writeConfig } from './config.js';
import { readSession, resetSessionForProfile } from './session.js';
import { VocaDaemon } from './daemon.js';

const require = createRequire(import.meta.url);
const pkg = require('../package.json');

const VALID_PROFILES = ['personal', 'public'];

export const program = new Command();

program
  .name('voca')
  .version(pkg.version)
  .description('Voice Operated Claw Assistant');

// --- session ---

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

// --- profile ---

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

// --- placeholder commands ---

program
  .command('start')
  .description('Start the voice assistant daemon')
  .option('--daemon', 'Run as a background daemon')
  .action(async (opts: { daemon?: boolean }) => {
    if (opts.daemon) {
      console.log('Not yet implemented — use foreground mode');
      return;
    }

    const config = await readConfig();
    const daemon = new VocaDaemon(config);

    const shutdown = async () => {
      console.log('\nShutting down…');
      await daemon.stop();
      process.exit(0);
    };

    process.on('SIGINT', shutdown);
    process.on('SIGTERM', shutdown);

    await daemon.start();
  });

program
  .command('stop')
  .description('Stop the voice assistant daemon')
  .action(() => {
    console.log('Daemon mode not yet available — use Ctrl+C to stop foreground daemon');
  });

program
  .command('status')
  .description('Show daemon status')
  .action(() => {
    console.log('Daemon mode not yet available — daemon not running');
  });

program
  .command('bootstrap')
  .description('Interactive setup for mic/speaker/dependencies')
  .action(() => {
    console.log('Not yet implemented');
  });

// --- main guard ---

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  program.parse();
}
