import chalk from 'chalk';
import ora, { Ora } from 'ora';
import Table from 'cli-table3';

export interface LoggerOptions {
  quiet?: boolean;
  json?: boolean;
  color?: boolean;
  verbose?: boolean;
}

export function createLogger(opts: LoggerOptions = {}) {
  const isQuiet = opts.quiet || false;
  const isJson = opts.json || false;
  const isVerbose = opts.verbose || false;

  if (opts.color === false) {
    chalk.level = 0;
  }

  const shouldLog = (): boolean => !isQuiet && !isJson;

  return {
    info(msg: string) {
      if (shouldLog()) console.log(msg);
    },

    success(msg: string) {
      if (shouldLog()) console.log(chalk.green('✔') + ' ' + msg);
    },

    warn(msg: string) {
      if (shouldLog()) console.log(chalk.yellow('⚠') + ' ' + msg);
    },

    error(msg: string) {
      console.error(chalk.red('✖') + ' ' + msg);
    },

    debug(msg: string) {
      if (isVerbose && shouldLog()) {
        console.log(chalk.gray('  [debug] ' + msg));
      }
    },

    spinner(text: string): Ora {
      if (isQuiet || isJson) {
        return {
          start() { return this as any; },
          stop() {},
          succeed(_t?: string) {},
          fail(_t?: string) {},
          set text(_t: string) {},
          get text() { return ''; },
        } as any;
      }
      return ora({ text, color: 'cyan' });
    },

    table(headers: string[], rows: string[][]) {
      if (isJson) {
        const data = rows.map(row => {
          const obj: Record<string, string> = {};
          headers.forEach((h, i) => { obj[h.toLowerCase()] = row[i]; });
          return obj;
        });
        console.log(JSON.stringify(data, null, 2));
        return;
      }
      if (isQuiet) return;

      const t = new Table({
        head: headers.map(h => chalk.cyan(h)),
        style: { head: [], border: [] },
      });
      rows.forEach(row => t.push(row));
      console.log(t.toString());
    },

    json(data: unknown) {
      console.log(JSON.stringify(data, null, 2));
    },

    newline() {
      if (shouldLog()) console.log();
    },
  };
}
