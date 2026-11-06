/** Invoke npm bundled with the selected Node, independent of global Windows shims. */
import {dirname,join} from 'node:path';
import {existsSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
const cli=join(dirname(process.execPath),process.platform==='win32'?'node_modules':'../lib/node_modules','npm/bin/npm-cli.js');
if(!existsSync(cli))throw new Error('Selected Node installation has no bundled npm CLI');
const result=spawnSync(process.execPath,[cli,...process.argv.slice(2)],{stdio:'inherit',env:process.env});
if(result.error)throw result.error;
process.exitCode=result.status??1;
