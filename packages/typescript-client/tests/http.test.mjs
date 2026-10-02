/** Actual loopback HTTP exercises SDK routing, bearer isolation and write non-replay. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Client,MemoryClient,MemoryApiError,RequestTimeoutError,AbortError,TimeoutError,PipelineDeadLettered,InputValidationError,RateLimited,NumericPrecisionError,clearHostCache,clearMetadataCache} from '../dist/index.js';
const fixtures=JSON.parse(readFileSync(new URL('./fixtures/python-methods.json',import.meta.url))).responses;
const id='10000000-0000-0000-0000-000000000001';
process.env.REMEMBER_CONFIG_DIR=join(tmpdir(),`remember-http-fixture-${process.pid}`);
for(const name of ['REMEMBER_API_URL','REMEMBER_API_KEY','REMEMBER_PROJECT','REMEMBER_ISSUER'])delete process.env[name];

/** Start an isolated server and force-close held sockets during fixture cleanup. */
async function server({handler}) {
  const requests=[];
  const http=createServer(async(request,response)=>{
    const buffers=[];for await(const chunk of request)buffers.push(chunk);
    const recorded={method:request.method,url:request.url,headers:request.headers,body:Buffer.concat(buffers)};requests.push(recorded);
    try {await handler({request,response,recorded});}catch(error){response.destroy(error);}
  });
  await new Promise(resolve=>http.listen(0,'127.0.0.1',resolve));
  return {url:`http://127.0.0.1:${http.address().port}`,requests,
    /** Close without waiting for a deliberately hanging test response. */
    close:async()=>{http.closeAllConnections();await new Promise(resolve=>http.close(resolve));}};
}
/** Send JSON from a real HTTP server. */
function json({response,value,status=200,headers={}}) {response.writeHead(status,{'content-type':'application/json',...headers});response.end(JSON.stringify(value));}
/** Produce routing claims only; no production key or authentic signature. */
function signedKey({issuer}) {return 'eyJhbGciOiJFUzI1NiJ9.'+Buffer.from(JSON.stringify({iss:issuer,projects:['p1','p2']})).toString('base64url')+'.fixture';}
/** Start two engines and one issuer that moves only after the first engine request. */
async function moving({firstOutcome='421',successfulValue=fixtures.ingest}) {
  clearMetadataCache();clearHostCache();let moved=false;
  const next=await server({handler:({response})=>json({response,value:successfulValue})});
  const first=await server({handler:({response,request})=>{
    moved=true;
    if(firstOutcome==='network')request.socket.destroy();
    else if(firstOutcome==='timeout')return;
    else if(firstOutcome==='engine-404')json({response,value:{detail:'not found'},status:404});
    else json({response,value:{message:'moved'},status:Number(firstOutcome)});
  }});
  let issuer;
  issuer=await server({handler:({recorded,response})=>{
    if(recorded.url.startsWith('/.well-known/'))json({response,value:{issuer:issuer.url,remember_project_endpoint:issuer.url+'/project'}});
    else json({response,value:{project:'p1',name:'fixture',api_url:moved?next.url:first.url}});
  }});
  return {first,next,issuer,key:signedKey({issuer:issuer.url}),
    /** Close all task-owned servers. */
    close:async()=>{await Promise.all([first.close(),next.close(),issuer.close()]);}};
}

for(const firstOutcome of ['421','404','network'])for(const kind of ['ingest','delete','connector','pause','assured'])test(`${kind} is never replayed after ${firstOutcome}; next call uses fresh mapping`,async()=>{
  const setup=await moving({firstOutcome});const client=new Client({apiKey:setup.key});
  const actions={ingest:()=>client.ingest({content:new Uint8Array([1]),filename:'note.md'}),delete:()=>client.deleteDocument({docId:id}),connector:()=>client.addConnector({connector:{kind:'custom',name:'fixture'}}),pause:()=>client.pauseConnector({connectorId:id}),assured:()=>client.runOperation({name:'facts_context',arguments:{query:'fixture'}})};
  try {
    await assert.rejects(actions[kind](),MemoryApiError);
    assert.equal(setup.first.requests.length,1);assert.equal(setup.next.requests.length,0);
    assert.equal(setup.issuer.requests.filter(r=>r.url.startsWith('/project')).length,2);
    await client.ingest({content:new Uint8Array([1]),filename:'second.md'});
    assert.equal(setup.next.requests.length,1);
    assert.equal(setup.next.requests[0].headers.authorization,`Bearer ${setup.key}`);
    assert.equal(new URL(setup.issuer.requests.at(-1).url,setup.issuer.url).searchParams.get('project'),'p1');
  }finally{client.close();await setup.close();}
});

for(const firstOutcome of ['421','404','network'])test(`read-only POST replays once after ${firstOutcome} only at changed mapping`,async()=>{
  const setup=await moving({firstOutcome,successfulValue:fixtures.query});const client=new Client({apiKey:setup.key});
  try {
    assert.deepEqual(await client.querySql({sql:'SELECT 1'}),fixtures.query);
    assert.equal(setup.first.requests.length,1);assert.equal(setup.next.requests.length,1);
    assert.equal(setup.next.requests[0].method,'POST');assert.equal(JSON.parse(setup.next.requests[0].body).sql,'SELECT 1');
  }finally{client.close();await setup.close();}
});

test('engine-shaped 404 and timeout never refresh or replay',async()=>{
  for(const firstOutcome of ['engine-404','timeout']) {
    const setup=await moving({firstOutcome});const client=new Client({apiKey:setup.key,timeoutMs:100});
    try {
      await assert.rejects(client.listOperations(),firstOutcome==='timeout'?RequestTimeoutError:MemoryApiError);
      assert.equal(setup.first.requests.length,1);assert.equal(setup.next.requests.length,0);
      assert.equal(setup.issuer.requests.filter(r=>r.url.startsWith('/project')).length,1);
    }finally{client.close();await setup.close();}
  }
});

test('engine redirects are refused and do not send a bearer to their target',async()=>{
  const trap=await server({handler:({response})=>json({response,value:[]})});
  const engine=await server({handler:({response})=>{response.writeHead(307,{location:trap.url+'/operations'});response.end();}});
  const client=new Client({apiKey:'shared-fixture',baseUrl:engine.url});
  try{await assert.rejects(client.listOperations(),error=>error.statusCode===307);assert.equal(trap.requests.length,0);}
  finally{client.close();await Promise.all([trap.close(),engine.close()]);}
});

test('numeric precision, malformed lists and admission errors retain public types',async()=>{
  let responseKind='precision';const engine=await server({handler:({response})=>{
    if(responseKind==='precision'){response.writeHead(200,{'content-type':'application/json'});response.end('{"unsafe":9007199254740993}');}
    else if(responseKind==='list')json({response,value:[{kind:'view',name:'fixture',score:1,purpose:'fixture',tags:[]},{kind:'view'}]});
    else json({response,status:429,headers:{'retry-after':'7'},value:{detail:{code:'concurrency_limited',message:'limited'}}});
  }});
  const client=new Client({apiKey:'fixture',baseUrl:engine.url});
  try {
    await assert.rejects(client.describeQuerySpace(),NumericPrecisionError);
    responseKind='list';await assert.rejects(client.searchQuerySpace({query:'fixture'}),MemoryApiError);
    responseKind='admission';await assert.rejects(client.listOperations(),error=>error instanceof RateLimited&&error.retryAfter===7&&error.code==='concurrency_limited');
    assert.equal(engine.requests.length,3);
  }finally{client.close();await engine.close();}
});

test('account paths and redirects stay within the issuer origin',async()=>{
  clearMetadataCache();const trap=await server({handler:({response})=>json({response,value:{}})});let issuer;
  issuer=await server({handler:({recorded,response})=>{
    if(recorded.url.startsWith('/.well-known'))json({response,value:{issuer:issuer.url,remember_account_endpoint:issuer.url+'/account'}});
    else if(recorded.url==='/account/v1/keys/self'){response.writeHead(307,{location:'/account/final'});response.end();}
    else if(recorded.url==='/account/final')json({response,value:{key_id:'fixture'}});
    else {response.writeHead(307,{location:trap.url+'/stolen'});response.end();}
  }});
  const client=Client.fromEnv({apiKey:signedKey({issuer:issuer.url})});const account=client.account;
  try {
    assert.deepEqual(await account.whoami(),{key_id:'fixture'});
    for(const path of ['https://example.com','//other','../other','/one/%2e%2e/other','/one/%252e%252e/other','/one/%5cother','/one/../../other'])await assert.rejects(account.get({path}),InputValidationError);
    await assert.rejects(account.get({path:'/redirect'}));assert.equal(trap.requests.length,0);
    for(const request of issuer.requests.filter(r=>!r.url.startsWith('/.well-known')))assert(request.headers.authorization.startsWith('Bearer '));
    client.close();await assert.rejects(account.whoami(),AbortError);
  }finally{client.close();await Promise.all([issuer.close(),trap.close()]);}
});

test('close cancels ignored-signal adapters; per-client state stays independent',async()=>{
  let called;const started=new Promise(resolve=>{called=resolve;});
  const one=new MemoryClient({client:{request:async()=>{called();return new Promise(()=>{});}}});
  const two=new MemoryClient({client:{request:async()=>Response.json([])}});
  const pending=one.listOperations();await started;one.close();await assert.rejects(pending,AbortError);
  assert.deepEqual(await two.listOperations(),[]);await assert.rejects(one.listOperations(),AbortError);two[Symbol.dispose]();
  assert.throws(()=>new MemoryClient({client:{request:async()=>Response.json([])},baseUrl:'http://localhost'}),InputValidationError);
});

/** Construct one readiness fixture with a chosen pipeline stage status. */
function report({status,ready=false}) {return {...fixtures.readiness,ready,versions:[{version_id:id,ready,stages:[{stage:'claims',status,component_version:'fixture',finished_at:null,defer_reason:null}]}]};}
test('readiness checks immediately, continues failed, and stops on dead letters',async()=>{
  let reports=[report({status:'failed'}),report({status:'succeeded',ready:true})];
  const client=new MemoryClient({client:{request:async()=>Response.json(reports.shift())}});
  try {
    const result=await client.waitForReadiness({versionIds:[id],pollIntervalMs:1,timeoutMs:1000});assert(result.ready);
    reports=[report({status:'dead_letter'})];await assert.rejects(client.waitForReadiness({versionIds:[id]}),error=>error instanceof PipelineDeadLettered&&error.deadLettered[0][1]==='claims'&&error.report.versions.length===1);
  }finally{client.close();}
});

test('readiness deadline retains last report, but an earlier HTTP timeout remains distinct',async()=>{
  const last=report({status:'running'});let calls=0;
  const client=new MemoryClient({client:{request:async()=>{calls++;return calls===1?Response.json(last):new Promise(()=>{});}}});
  try{await assert.rejects(client.waitForReadiness({versionIds:[id],pollIntervalMs:1,timeoutMs:20}),error=>error instanceof TimeoutError&&error.report.versions[0].stages[0].status==='running');}
  finally{client.close();}
  const early=new MemoryClient({timeoutMs:5,client:{request:async()=>new Promise(()=>{})}});
  try{await assert.rejects(early.waitForReadiness({versionIds:[id],timeoutMs:1000}),RequestTimeoutError);}
  finally{early.close();}
});

test('account discovery outages retain IssuerError',async()=>{
  clearMetadataCache();
  const issuer=await server({handler:({response})=>json({response,value:{detail:'synthetic unavailable'},status:503})});
  const {IssuerError}=await import('../dist/index.js');
  const client=new Client({apiKey:signedKey({issuer:issuer.url})});
  try{await assert.rejects(client.account.whoami(),IssuerError);}finally{client.close();await issuer.close();}
});

test('a delayed read retries at the new host when another request already repinned',async()=>{
  clearMetadataCache();clearHostCache();let moved=false,release,arrived;
  const held=new Promise(resolve=>{release=resolve;});const started=new Promise(resolve=>{arrived=resolve;});
  const next=await server({handler:({response})=>json({response,value:[]})});
  const first=await server({handler:async({recorded,response})=>{
    if(recorded.method==='GET'){arrived();await held;}
    else moved=true;
    json({response,value:{message:'moved'},status:421});
  }});
  let issuer;issuer=await server({handler:({recorded,response})=>json({response,value:recorded.url.startsWith('/.well-known')?{issuer:issuer.url,remember_project_endpoint:issuer.url+'/project'}:{project:'p1',name:'fixture',api_url:moved?next.url:first.url}})});
  const client=new Client({apiKey:signedKey({issuer:issuer.url})});
  try{
    const read=client.listOperations();await started;
    await assert.rejects(client.ingest({content:Buffer.from('fixture'),filename:'note.md'}),error=>error.statusCode===421);
    release();assert.deepEqual(await read,[]);
    assert.equal(first.requests.length,2);assert.equal(next.requests.length,1);assert.equal(next.requests[0].method,'GET');
  }finally{release();client.close();await Promise.all([issuer.close(),first.close(),next.close()]);}
});

test('finite positive request/readiness deadlines refuse before sending',async()=>{
  let calls=0;const adapter={request:async()=>{calls++;return Response.json(fixtures.readiness);}};
  for(const invalid of [0,-1,NaN,Infinity]){
    assert.throws(()=>new Client({client:adapter,timeoutMs:invalid}),InputValidationError);
    const client=new Client({client:adapter});
    try{
      await assert.rejects(client.waitForReadiness({versionIds:[id],timeoutMs:invalid}),InputValidationError);
      await assert.rejects(client.waitForReadiness({versionIds:[id],pollIntervalMs:invalid}),InputValidationError);
    }finally{client.close();}
  }
  assert.equal(calls,0);
});
