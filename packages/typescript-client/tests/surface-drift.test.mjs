/** Prove public export and invisible runtime-default mutations fail the drift gate. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cpSync,mkdtempSync,mkdirSync,writeFileSync,readFileSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
for(const [name,file,before,after]of [
  ['root resolver','index.ts','Connection,resolveConnection,','Connection,'],
  ['support security helpers','index.ts','isLoopback,requireSecureUrl,',''],
  ['request timeout','client.ts','timeoutMs=30000','timeoutMs=1'],
  ['readiness timeout','client.ts','timeoutMs=1800000','timeoutMs=1'],
  ['poll interval','client.ts','pollIntervalMs=15000','pollIntervalMs=1'],
])test(`drift rejects missing/changed ${name}`,()=>{
  const directory=mkdtempSync(join(tmpdir(),'remember-surface-mutation-'));
  try{
    cpSync(join(root,'src'),join(directory,'src'),{recursive:true});mkdirSync(join(directory,'contracts'));
    cpSync(join(root,'contracts/parity.json'),join(directory,'contracts/parity.json'));
    const path=join(directory,'src',file);const original=readFileSync(path,'utf8');assert(original.includes(before));writeFileSync(path,original.replace(before,after));
    const check=spawnSync(process.execPath,[join(root,'scripts/check-surface.mjs'),directory],{encoding:'utf8',timeout:30000});
    assert.notEqual(check.status,0);assert.match(check.stderr,/Missing reviewed public export|default drift/);
  }finally{rmSync(directory,{recursive:true,force:true});}
});
