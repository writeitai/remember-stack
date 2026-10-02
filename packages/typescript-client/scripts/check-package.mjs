/** Install the actual tarball in independent ESM/CommonJS typed consumers. */
import {execFileSync} from 'node:child_process';
import {mkdtempSync,writeFileSync,readFileSync,rmSync} from 'node:fs';
import {join,dirname,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const npmCli=process.env.npm_execpath;
if(!npmCli)throw new Error('Run this check through npm run check:package');

/** Run npm with an argument vector rather than shell-dependent Windows quoting.
 * @param {{args: string[], cwd: string}} options
 * @returns {string}
 */
function npm({args,cwd}) {return execFileSync(process.execPath,[npmCli,...args],{cwd,encoding:'utf8',stdio:['ignore','pipe','pipe']});}
/** Check published file contents, runtime imports and NodeNext declaration resolution.
 * @returns {void}
 */
function main() {
  const directory=mkdtempSync(join(tmpdir(),'remember-ts-tarball-'));
  try {
    const [packed]=JSON.parse(npm({args:['pack','--json','--pack-destination',directory],cwd:root}));
    const names=packed.files.map(file=>file.path);
    if(names.some(name=>name.startsWith('src/')||name.startsWith('tests/')||name.startsWith('contracts/')||name.startsWith('scripts/')||name.includes('credentials.json')))throw new Error('Development/credential files leaked into package');
    for(const name of ['dist/index.js','dist/index.cjs','dist/index.d.ts','dist/index.d.cts','README.md','LICENSE'])if(!names.includes(name))throw new Error(`Tarball missing ${name}`);
    const metadata=JSON.parse(readFileSync(join(root,'package.json'),'utf8'));
    if(metadata.bin||Object.keys(metadata.exports).join()!=='.')throw new Error('Base client acquired executable or unintended subpath exports');
    writeFileSync(join(directory,'package.json'),JSON.stringify({name:'remember-tarball-consumer',private:true,type:'module'}));
    npm({args:['install','--ignore-scripts','--no-audit','--no-fund',join(directory,packed.filename),`typescript@${metadata.devDependencies.typescript}`,`@types/node@${metadata.devDependencies['@types/node']}`],cwd:directory});
    const checks=`
const client = new sdk.Client({client:{request:async()=>Response.json([])}});
if(sdk.Client !== sdk.RememberClient || sdk.memoryTools().length!==18)throw new Error('public exports are incomplete');
client.listOperations().then(operations=>{if(operations.length!==0)throw new Error('invalid response');client.close();});
/** Verify filled response fields and recursive JSON typing in declarations. */
function responseTypes({query,envelope}:{query:sdk.QueryResultDict;envelope:sdk.Envelope}):void {
  const rows:sdk.JsonValue[][]=query.rows;
  const timestamp:string=envelope.freshness.pg_live_ts;
  const defaults:boolean=envelope.freshness.p1_written_inline;
  void rows;void timestamp;void defaults;
}
const readTime:sdk.ReadTimeInput={mode:'overlap',from:'2026-01-01T00:00:00Z',to:'2026-07-01T00:00:00Z'};
const sectionRequest:sdk.SectionHistoryRequestInput={doc_id:'10000000-0000-0000-0000-000000000001',section_key:'part/4',time:readTime};
const referencesRequest:sdk.DocumentReferencesRequestInput={doc_id:sectionRequest.doc_id,time:readTime};
const reference:sdk.ReferenceInputInput={kind:'amends',target:{source_kind:'policy',source_ref:'expenses'},change_date_known:false};
void sectionRequest;void referencesRequest;void reference;
const input:sdk.DocumentSearchFiltersInput={authors:['alice']};void input;void responseTypes;
/** Verify incorrect public inputs are rejected by the installed declarations. */
function inputTypes():void {
  // @ts-expect-error SQL text is not a number.
  void client.querySql({sql:123});
  // @ts-expect-error Defaulted response fields are available, not optional.
  const invalid:sdk.DocumentSearchFilters={authors:['alice']};void invalid;
}
void inputTypes;
`;
    writeFileSync(join(directory,'consumer.mts'),`import * as sdk from '@rememberdev/client';\n${checks}`);
    writeFileSync(join(directory,'consumer.cts'),`import sdk = require('@rememberdev/client');\n${checks}`);
    execFileSync(process.execPath,[join(directory,'node_modules/typescript/bin/tsc'),'--strict','--module','NodeNext','--moduleResolution','NodeNext','--target','ES2022','--lib','ES2022,ESNext.Disposable','consumer.mts','consumer.cts'],{cwd:directory,stdio:'inherit'});
    for(const consumer of ['consumer.mjs','consumer.cjs'])execFileSync(process.execPath,[join(directory,consumer)],{cwd:directory,stdio:'inherit'});
    console.log(`Tarball verified: ${packed.filename}; ESM/CommonJS runtime and declarations pass`);
  }finally{rmSync(directory,{recursive:true,force:true});}
}
main();
