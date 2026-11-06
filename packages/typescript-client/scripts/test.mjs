/** Pass explicit test paths to Node on shells without wildcard expansion. */
import {readdirSync} from 'node:fs';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
const files=readdirSync('tests').filter(name=>name.endsWith('.test.mjs')).sort().map(name=>join('tests',name));
if(!files.length)throw new Error('No client tests found');
const result=spawnSync(process.execPath,['--test',...files],{stdio:'inherit',env:process.env});
if(result.error)throw result.error;
process.exitCode=result.status??1;
