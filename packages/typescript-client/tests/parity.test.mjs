/** Execute all source-recorded methods and compare exact wire/default/result behavior. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {Client,MemoryClient} from '../dist/index.js';
const fixture=JSON.parse(readFileSync(new URL('./fixtures/python-methods.json',import.meta.url)));
const inventory=JSON.parse(readFileSync(new URL('../contracts/parity.json',import.meta.url)));

for(const scenario of fixture.cases)test(`${scenario.typescriptMethod}: Python ${scenario.variant} parity`,async()=>{
  const directory=mkdtempSync(join(tmpdir(),'remember-ts-parity-'));const path=join(directory,'fixture.md');writeFileSync(path,'note');
  const wire=[];
  const adapter={request:async request=>{
    const headers=new Headers(request.headers);const body=request.body;
    wire.push({method:request.method,path:request.path,query:Array.from(request.query??[]),contentType:headers.get('content-type'),body:headers.get('content-type')==='application/json'?JSON.parse(body):null,contentBase64:body&&headers.get('content-type')!=='application/json'?Buffer.from(body).toString('base64'):null});
    return Response.json(scenario.responses[wire.length-1]);
  }};
  const client=new Client({client:adapter});
  const options={...scenario.typescriptOptions};
  for(const key of ['source','filePath'])if(options[key]==='$FILE')options[key]=path;
  if(options.content?.base64)options.content=Buffer.from(options.content.base64,'base64');
  try {
    assert.deepEqual(await client[scenario.typescriptMethod](options),scenario.result);
    assert.deepEqual(wire,scenario.wire);
  }finally {client.close();rmSync(directory,{force:true,recursive:true});}
});

test('the matrix covers every reviewed method and new methods fail coverage',()=>{
  const covered=new Set(fixture.cases.map(c=>c.method));
  for(const [className,methods]of Object.entries(inventory.classes))for(const name of Object.keys(methods)) {
    assert(covered.has(name)||fixture.lifecycleDispositions[`${className}.${name}`],`${className}.${name} lacks an executable conformance disposition`);
  }
  const camel=value=>value.replace(/_([a-z])/g,(_,letter)=>letter.toUpperCase());
  const prototype=Object.getOwnPropertyNames(MemoryClient.prototype).filter(name=>!['constructor','json','search'].includes(name)&&typeof Object.getOwnPropertyDescriptor(MemoryClient.prototype,name).value==='function');
  assert.deepEqual(prototype.sort(),Object.keys(inventory.classes.MemoryClient).filter(name=>name!=='__init__').map(camel).sort());
});
