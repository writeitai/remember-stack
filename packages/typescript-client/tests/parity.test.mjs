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
  const prototype=Object.getOwnPropertyNames(MemoryClient.prototype).filter(name=>!['constructor','json','search','assertOpen'].includes(name)&&typeof Object.getOwnPropertyDescriptor(MemoryClient.prototype,name).value==='function');
  assert.deepEqual(prototype.sort(),Object.keys(inventory.classes.MemoryClient).filter(name=>name!=='__init__').map(camel).sort());
});

for(const scenario of fixture.failures)test(`${scenario.typescriptMethod}: shared ${scenario.variant} refusal ${JSON.stringify(scenario.body).slice(0,50)}`,async()=>{
  let calls=0;
  const client=new Client({client:{request:async()=>{calls++;return Response.json(scenario.body,{status:scenario.status,headers:scenario.headers});}}});
  const options={...scenario.typescriptOptions};if(options.content?.base64)options.content=Buffer.from(options.content.base64,'base64');
  try{
    await assert.rejects(client[scenario.typescriptMethod](options),error=>{
      assert.equal(error.name,scenario.error.class);assert.equal(error.statusCode,scenario.error.statusCode);
      if(scenario.variant.startsWith('admission'))assert.equal(error.detail,scenario.error.detail);
      for(const key of ['code','retryable','requestId','retryAfter'])assert.equal(error[key]??null,scenario.error[key]??null);
      return true;
    });assert.equal(calls,1);
  }finally{client.close();}
});


for(const scenario of fixture.accountErrors)test(`account ${scenario.status}/${scenario.path}: Python error decoder parity`,async()=>{
  const client=new Client({apiKey:scenario.key,baseUrl:'https://engine-fixture.invalid',transport:{request:async request=>request.url.includes('/.well-known')?Response.json({issuer:scenario.issuer,remember_account_endpoint:scenario.issuer+'/account'}):Response.json(scenario.body,{status:scenario.status})}});
  try{await assert.rejects(client.account.get({path:scenario.path}),error=>{
    for(const key of ['name','detail','statusCode','code'])assert.equal(error[key]??null,scenario.error[key==='name'?'class':key]??null);
    return true;
  });}finally{client.close();}
});

for(const scenario of fixture.issuerRedirects)test(`${scenario.stage} ${scenario.status}/${scenario.location??'missing Location'}: Python issuer redirect parity`,async()=>{
  const {AccountApi,Connection,SecretString,signedKeyClaims,resolveProject,fetchIssuerMetadata,clearHostCache,clearMetadataCache}=await import('../.test-build/internal.js');
  clearHostCache();clearMetadataCache();const wire=[];
  const http={request:async request=>{
    const response=scenario.responses[wire.length];assert(response,'redirect sent an extra request');
    wire.push({method:request.method,url:request.url});
    return new Response(response.body===null?null:JSON.stringify(response.body),{status:response.status,headers:{...response.headers,...(response.body===null?{}:{'content-type':'application/json'})}});
  }};
  const claims=signedKeyClaims({key:scenario.key});
  const connection=new Connection({key:new SecretString({value:scenario.key}),keySource:'explicit',apiUrl:null,apiUrlSource:null,project:null,issuer:scenario.issuer,mcpUrl:null,stored:null,claims});
  const operation=scenario.stage==='account'?()=>new AccountApi({connection,http}).get({path:'/v1/keys/self'}):scenario.stage==='metadata'?()=>fetchIssuerMetadata({issuer:scenario.issuer,http}):()=>resolveProject({key:scenario.key,claims,project:null,http});
  try {
    if(scenario.error)await assert.rejects(operation(),error=>{for(const key of ['name','statusCode','code'])assert.equal(error[key]??null,scenario.error[key==='name'?'class':key]??null);return true;});
    else assert.deepEqual({...await operation()},scenario.result);
    assert.deepEqual(wire,scenario.wire);
  }finally{clearHostCache();clearMetadataCache();}
});
