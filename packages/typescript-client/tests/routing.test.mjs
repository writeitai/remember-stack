/** Exercise routing trust boundaries, shared discovery and complete request deadlines. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,chmodSync,symlinkSync,mkdirSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {inspect} from 'node:util';
import {bounded,checkedResponse,MemoryApiError,RequestTimeoutError,AbortError,CredentialError,loadCredentials,normalizeKey,signedKeyClaims,normalizeIssuer,metadataUrl,sameOrigin,fetchIssuerMetadata,clearMetadataCache,clearHostCache,resolveConnection,EngineRoute,SecretString,Connection,StoredKeyRefused} from '../.test-build/internal.js';

/** Create a routing-only token with no authentic signature or production credential. */
function key({issuer='https://issuer.example',projects=['p1','p2'],jti='same-id'}={}) {
  return `eyJhbGciOiJFUzI1NiJ9.${Buffer.from(JSON.stringify({iss:issuer,projects,jti})).toString('base64url')}.fixture`;
}
/** Start a fresh, isolated issuer/client fixture. */
function fixture({apiKey=key(),clock=()=>0,resolve=()=>({project:'p1',name:'one',api_url:'https://engine.example'})}={}) {
  clearMetadataCache();clearHostCache();const calls=[];
  const http={request:async request=>{
    calls.push(request);
    if(request.url.includes('.well-known')) return Response.json({issuer:'https://issuer.example',remember_project_endpoint:'https://issuer.example/resolve'});
    return Response.json(await resolve(request));
  }};
  const connection=resolveConnection({apiKey,env:{configDir:join(tmpdir(),'remember-no-such-directory-fixture')}});
  return {route:new EngineRoute({connection,http,clock}),http,calls,connection};
}

test('complete deadlines reject hanging work and caller timeout signals remain cancellation',async()=>{
  await assert.rejects(bounded({timeoutMs:5,work:async()=>new Promise(()=>{})}),RequestTimeoutError);
  const reason=new DOMException('caller deadline','TimeoutError');const abort=new AbortController();
  abort.abort(reason);
  await assert.rejects(bounded({signal:abort.signal,work:async()=>0}),error=>error instanceof AbortError && error.cause===reason);
  const body=new ReadableStream({start(){}});
  await assert.rejects(bounded({timeoutMs:5,work:async()=>checkedResponse({response:new Response(body),path:'/deployment'})}),RequestTimeoutError);
});

test('query diagnostics require the exact code/status and typed optional fields',async()=>{
  await assert.rejects(checkedResponse({path:'/query/sql',response:Response.json({detail:{code:'pg_unavailable',message:'offline',retryable:true,request_id:'fixture'}},{status:503})}),error=>error instanceof MemoryApiError&&error.code==='pg_unavailable'&&error.retryable===true&&error.requestId==='fixture');
  for(const detail of [{code:'pg_unavailable',message:'offline',retryable:'yes'},{code:'pg_unavailable',message:'offline',extra:1},{code:'pg_unavailable',message:''}]) {
    await assert.rejects(checkedResponse({path:'/query/sql',response:Response.json({detail},{status:503})}),error=>error.code===undefined&&error.detail.includes('malformed'));
  }
  await assert.rejects(checkedResponse({path:'/query/sql',response:Response.json({detail:{code:'pg_unavailable',message:'wrong status'}},{status:422})}),error=>error.code===undefined);
});

test('key and issuer helpers fail closed without exposing bearer secrets',()=>{
  assert.equal(normalizeKey({value:'  Bearer shared-fixture '}),'shared-fixture');
  assert.throws(()=>normalizeKey({value:'one\ntwo'}));
  assert.equal(signedKeyClaims({key:'shared-fixture'}),null);
  assert.throws(()=>signedKeyClaims({key:'eyJ.not-jws'}));
  assert.equal(signedKeyClaims({key:`dev_${key()}`}).covers({projectId:'p1'}),true);
  assert.equal(metadataUrl({issuer:'https://issuer.example/tenant/'}),'https://issuer.example/.well-known/oauth-authorization-server/tenant');
  assert.equal(sameOrigin({left:'https://EXAMPLE.com.:443/a',right:'https://example.com/b'}),true);
  for(const issuer of ['http://public.example','https://user:pass@issuer.example']) assert.throws(()=>normalizeIssuer({issuer}));
  const secret=new SecretString({value:'not-a-real-key'});
  assert.equal(JSON.stringify(secret),'"**********"');assert(!inspect(secret).includes('not-a-real-key'));
});

test('POSIX file reads reject readable, symlink and nonregular credentials',()=>{
  const dir=mkdtempSync(join(tmpdir(),'remember-credentials-'));const env={configDir:dir};const path=join(dir,'credentials.json');
  try {
    assert.equal(loadCredentials({env}),null);
    writeFileSync(path,JSON.stringify({version:2,api_url:'http://127.0.0.1:8000',key:'shared-fixture'}),{mode:0o600});
    if(process.platform==='win32') {assert.throws(()=>loadCredentials({env}),CredentialError);return;}
    assert.equal(loadCredentials({env}).key.getSecretValue(),'shared-fixture');
    chmodSync(path,0o644);assert.throws(()=>loadCredentials({env}),CredentialError);rmSync(path);
    writeFileSync(join(dir,'target'),'{}',{mode:0o600});symlinkSync(join(dir,'target'),path);assert.throws(()=>loadCredentials({env}),CredentialError);rmSync(path);
    mkdirSync(path);assert.throws(()=>loadCredentials({env}),CredentialError);
  } finally {rmSync(dir,{recursive:true,force:true});}
});

test('issuer identity and cross-origin advertised authenticated endpoints are refused',async()=>{
  clearMetadataCache();
  await assert.rejects(fetchIssuerMetadata({issuer:'https://issuer.example',http:{request:async()=>Response.json({issuer:'https://other.example'})}}));
  const {route}=fixture();
  await route.target();
  clearMetadataCache();clearHostCache();
  const connection=resolveConnection({apiKey:key(),env:{configDir:join(tmpdir(),'remember-none')}});
  const hostile=new EngineRoute({connection,http:{request:async()=>Response.json({issuer:'https://issuer.example',remember_project_endpoint:'https://other.example/resolve'})}});
  await assert.rejects(hostile.target());
});

test('concurrent first discovery is shared and TTL refresh pins the original project',async()=>{
  let now=0;let target='https://engine.example';let resolverCalls=0;
  const {route,calls,http,connection}=fixture({clock:()=>now,resolve:request=>{resolverCalls++;assert.equal(request.query?.get('project')??null,resolverCalls===1?null:'p1');return {project:'p1',name:'one',api_url:target};}});
  const second=new EngineRoute({connection,http,clock:()=>now});
  await Promise.all([route.target(),route.target(),second.target()]);assert.equal(calls.length,2);
  now=601;target='https://new-engine.example';assert.equal((await route.target()).url,target);assert.equal(resolverCalls,2);
});

test('a cancelling discovery waiter leaves another client and cache usable',async()=>{
  let release;const held=new Promise(resolve=>{release=resolve;});
  const {route,http,connection}=fixture({resolve:async()=>{await held;return {project:'p1',name:'one',api_url:'https://engine.example'};}});
  const second=new EngineRoute({connection,http});const controller=new AbortController();
  const first=route.target({signal:controller.signal});const other=second.target();
  controller.abort('fixture reason');await assert.rejects(first,AbortError);release();
  assert.equal((await other).url,'https://engine.example');assert.equal((await route.target()).url,'https://engine.example');
});

test('stored shared keys cannot leave their recorded origin',async()=>{
  const connection=new Connection({key:new SecretString({value:'shared-fixture'}),keySource:'file',apiUrl:'https://other.example',apiUrlSource:'explicit',project:null,issuer:null,mcpUrl:null,claims:null,stored:{version:2,key:new SecretString({value:'shared-fixture'}),api_url:'https://recorded.example',issuer:null,key_id:null,expires_at:null,default_project:null}});
  const route=new EngineRoute({connection,http:{request:async()=>{throw new Error('must not send');}}});
  await assert.rejects(route.target(),StoredKeyRefused);
  assert(!JSON.stringify(connection).includes('shared-fixture'));assert(!inspect(connection).includes('shared-fixture'));
});
