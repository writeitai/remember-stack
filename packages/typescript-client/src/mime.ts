import { extname } from 'node:path';
import database from 'mime-db';
import constants from './constants.generated';
const extensions = new Map<string,string>();
for (const [mime, entry] of Object.entries(database)) for (const extension of entry.extensions ?? []) {
  if (!extensions.has(extension) || entry.source === 'iana') extensions.set(extension,mime);
}
/** Infer MIME deterministically, with Python's explicit overrides taking priority. */
export function inferUploadMime({filename}:{filename:string}):string {
  const extension=extname(filename).toLowerCase();
  return (constants.mime as Record<string,string>)[extension] ?? extensions.get(extension.slice(1)) ?? 'application/octet-stream';
}
