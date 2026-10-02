import { inspect } from 'node:util';
import { constants as fsConstants, openSync, fstatSync, closeSync, readFileSync, existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import type { OutputStoredCredentials } from './generated';
import { CredentialError } from './errors';
import { parseJson } from './json';
import { validateModel } from './validation';

/** Secrets require deliberate access; inspect, string and JSON representations redact. */
export class SecretString {
  readonly #value: string;
  /** Keep the bearer outside enumerable/public object state. */
  constructor({ value }: { value: string }) { this.#value = value; }
  /** Deliberately access a credential for an authenticated request or CLI persistence. */
  getSecretValue(): string { return this.#value; }
  /** Redact JSON serialization. */
  toJSON(): string { return '**********'; }
  /** Redact ordinary display. */
  toString(): string { return '**********'; }
  /** Redact Node's object inspection. */
  [inspect.custom](): string { return 'SecretString(**********)'; }
}
/** Stored version-2 credentials use secret wrappers instead of public key strings. */
export type StoredCredentials = Omit<OutputStoredCredentials, 'key'> & { key: SecretString | null };
/** One place reads REMEMBER connection/config settings; tests can pass an isolated environment. */
export interface ConnectionEnvironment {
  apiKey?: string; apiUrl?: string; project?: string; issuer?: string; mcpUrl?: string;
  configDir?: string; xdgConfigHome?: string;
}

/** Read only documented environment names, treating empty connection values as unset. */
export function environment(): ConnectionEnvironment {
  return {
    apiKey: process.env.REMEMBER_API_KEY || undefined, apiUrl: process.env.REMEMBER_API_URL || undefined,
    project: process.env.REMEMBER_PROJECT || undefined, issuer: process.env.REMEMBER_ISSUER || undefined,
    mcpUrl: process.env.REMEMBER_MCP_URL || undefined, configDir: process.env.REMEMBER_CONFIG_DIR || undefined,
    xdgConfigHome: process.env.XDG_CONFIG_HOME || undefined,
  };
}
/** Return the shared Python/CLI configuration directory. */
export function configDir({ env = environment() }: { env?: ConnectionEnvironment } = {}): string {
  return env.configDir ?? (env.xdgConfigHome ? join(env.xdgConfigHome, 'remember') : join(homedir(), '.config', 'remember'));
}
/** Return the existing version-2 credential file location, never a second TS store. */
export function credentialsPath({ env = environment() }: { env?: ConnectionEnvironment } = {}): string {
  return join(configDir({ env }), 'credentials.json');
}
/** Read an owner-only regular file from an opened handle, with no symlink/FIFO wait. */
export function loadCredentials({ env = environment() }: { env?: ConnectionEnvironment } = {}): StoredCredentials | null {
  const path = credentialsPath({ env });
  if (process.platform === 'win32') {
    if (!existsSync(path)) return null;
    throw new CredentialError({ detail: 'automatic stored credentials are unavailable on Windows; pass explicit settings' });
  }
  let handle: number;
  try { handle = openSync(path, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW | fsConstants.O_NONBLOCK); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw new CredentialError({ detail: 'credential file is unreadable or a symlink' });
  }
  try {
    const info = fstatSync(handle);
    if (!info.isFile()) throw new CredentialError({ detail: 'credential file must be a regular file' });
    if (info.mode & 0o044) throw new CredentialError({ detail: 'credential file is readable by other users' });
    let data: OutputStoredCredentials;
    try { data = validateModel({ name: 'StoredCredentials', value: parseJson({ text: readFileSync(handle, 'utf8') }) }); }
    catch { throw new CredentialError({ detail: 'credential file is not a valid version-2 credential file' }); }
    if ((data.issuer !== null && (data.key === null || data.api_url !== null))
      || (data.issuer === null && data.api_url === null)) throw new CredentialError({ detail: 'credential file must contain one issuer or self-hosted entry' });
    return { ...data, key: data.key === null ? null : new SecretString({ value: data.key }) };
  } finally { closeSync(handle); }
}
