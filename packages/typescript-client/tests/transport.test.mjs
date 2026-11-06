/** Real HTTP/HTTPS requests prove single-send behavior, pooling and ownership. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Agent as HttpAgent,createServer as httpServer} from 'node:http';
import {Agent as HttpsAgent,createServer as httpsServer} from 'node:https';
import {execFileSync} from 'node:child_process';
import {existsSync,mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {nodeHttp,discoveryTransport,fetchIssuerMetadata,clearMetadataCache} from '../.test-build/internal.js';
import {MemoryClient,AbortError,RequestTimeoutError} from '../dist/index.js';
process.env.REMEMBER_CONFIG_DIR=join(tmpdir(),`remember-transport-fixture-${process.pid}`);
for(const name of ['REMEMBER_API_URL','REMEMBER_API_KEY','REMEMBER_PROJECT','REMEMBER_ISSUER'])delete process.env[name];
/** Delay only a socket test, without blocking other tests or the event loop. */
function delay({ms}) {return new Promise(resolve=>setTimeout(resolve,ms));}
/** Generate test-only certificates in a temporary directory, never the repository. */
function certificate() {
  const directory=mkdtempSync(join(tmpdir(),'remember-tls-'));
  const key=join(directory,'key.pem');const cert=join(directory,'cert.pem');
  const gitOpenSsl=join(process.env.ProgramFiles??'C:\\Program Files','Git','usr','bin','openssl.exe');
  const openssl=process.platform==='win32'&&existsSync(gitOpenSsl)?gitOpenSsl:'openssl';
  try {
    execFileSync(openssl,['req','-x509','-newkey','rsa:2048','-nodes','-keyout',key,'-out',cert,'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1,DNS:localhost'],{stdio:'ignore'});
    return {key:readFileSync(key),cert:readFileSync(cert),cleanup:()=>rmSync(directory,{recursive:true,force:true})};
  }catch(error){rmSync(directory,{recursive:true,force:true});throw error;}
}
/** Start a real server and identify the connection from the receiving side. */
async function server({secure=false,tls,handler}) {
  const seen=[];const sockets=new WeakMap();let next=0;
  const listener=(request,response)=>{
    if(!sockets.has(request.socket))sockets.set(request.socket,++next);
    seen.push({url:request.url,connection:sockets.get(request.socket),method:request.method});
    request.resume();
    Promise.resolve(handler({request,response})).catch(error=>response.destroy(error));
  };
  const server=secure?httpsServer(tls,listener):httpServer(listener);
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  return {url:`${secure?'https':'http'}://127.0.0.1:${server.address().port}`,seen,close:async()=>{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}};
}

for(const secure of [false,true]) {
  test(`${secure?'HTTPS':'HTTP'} 421 retires the connection before an already queued request`,async()=>{
    const tls=secure?certificate():undefined;
    const fixture=await server({secure,tls,handler:async({request,response})=>{
      if(request.url==='/moved'){response.writeHead(421);await delay({ms:50});response.end('original 421 body');}
      else response.end('next');
    }});
    const agents=secure?{https:new HttpsAgent({ca:tls.cert,keepAlive:true,maxSockets:1})}:{http:new HttpAgent({keepAlive:true,maxSockets:1})};
    const owned=nodeHttp({agents});
    try {
      const first=owned.transport.request({url:fixture.url+'/moved',method:'DELETE'});
      const queued=owned.transport.request({url:fixture.url+'/queued',method:'POST',body:'one'});
      assert.equal(await(await first).text(),'original 421 body');assert.equal(await(await queued).text(),'next');
      await owned.transport.request({url:fixture.url+'/later',method:'POST'});
      assert.equal(fixture.seen.length,3);
      assert.notEqual(fixture.seen[0].connection,fixture.seen[1].connection);
      assert.equal(fixture.seen[1].connection,fixture.seen[2].connection);
      owned.close();
      await owned.transport.request({url:fixture.url+'/borrowed-still-open',method:'GET'});
      assert.equal(fixture.seen[3].connection,fixture.seen[2].connection);
    }finally{owned.close();agents.http?.destroy();agents.https?.destroy();await fixture.close();tls?.cleanup();}
  });
}

test('owned idle sockets expire while active responses longer than four seconds succeed',async()=>{
  const fixture=await server({handler:async({request,response})=>{
    if(request.url==='/slow'){response.writeHead(200);response.write('start');await delay({ms:4500});response.end('end');}
    else response.end('quick');
  }});
  const owned=nodeHttp();
  try {
    await owned.transport.request({url:fixture.url+'/initial',method:'GET'});
    const active=owned.transport.request({url:fixture.url+'/slow',method:'GET',signal:AbortSignal.timeout(7000)});
    assert.equal(await(await active).text(),'startend');
    await delay({ms:4500});
    await owned.transport.request({url:fixture.url+'/after-idle',method:'GET'});
    assert.equal(fixture.seen[0].connection,fixture.seen[1].connection);
    assert.notEqual(fixture.seen[1].connection,fixture.seen[2].connection);
  }finally{owned.close();await fixture.close();}
});

test('TLS rejects an untrusted certificate and accepts a caller CA without owning its agent',async()=>{
  const tls=certificate();const fixture=await server({secure:true,tls,handler:({response})=>response.end('[]')});
  const defaultClient=new MemoryClient({baseUrl:fixture.url,apiKey:'fixture'});
  const agent=new HttpsAgent({ca:tls.cert,keepAlive:true});
  const client=new MemoryClient({baseUrl:fixture.url,apiKey:'fixture',agents:{https:agent}});
  try {
    await assert.rejects(defaultClient.listOperations(),error=>error.statusCode===0);
    assert.deepEqual(await client.listOperations(),[]);client.close();
    const next=new MemoryClient({baseUrl:fixture.url,apiKey:'fixture',agents:{https:agent}});
    try{assert.deepEqual(await next.listOperations(),[]);}finally{next.close();}
    assert.equal(fixture.seen[0].connection,fixture.seen[1].connection);
  }finally{client.close();defaultClient.close();agent.destroy();await fixture.close();tls.cleanup();}
});

test('continuous chunks still hit the total operation deadline',async()=>{
  let timer;
  const fixture=await server({handler:({response})=>{
    response.writeHead(200,{'content-type':'application/json'});response.write('[');
    timer=setInterval(()=>response.write(' '),10);response.once('close',()=>clearInterval(timer));
  }});
  const client=new MemoryClient({apiKey:'fixture',baseUrl:fixture.url,timeoutMs:120});
  try{await assert.rejects(client.listOperations(),RequestTimeoutError);assert.equal(fixture.seen.length,1);}
  finally{client.close();clearInterval(timer);await fixture.close();}
});

test('closing one client cannot cancel another waiter on default shared discovery',async()=>{
  clearMetadataCache();let release;let entered;
  const started=new Promise(resolve=>{entered=resolve;});const held=new Promise(resolve=>{release=resolve;});
  let issuer;
  issuer=await server({handler:async({request,response})=>{
    if(request.url.startsWith('/.well-known')){entered();await held;response.end(JSON.stringify({issuer:issuer.url,remember_project_endpoint:issuer.url+'/project'}));}
    else if(request.url.startsWith('/project'))response.end(JSON.stringify({project:'p1',name:'one',api_url:issuer.url}));
    else response.end('[]');
  }});
  const key='eyJhbGciOiJFUzI1NiJ9.'+Buffer.from(JSON.stringify({iss:issuer.url,projects:['p1']})).toString('base64url')+'.fixture';
  const one=new MemoryClient({apiKey:key});const two=new MemoryClient({apiKey:key});
  try {
    const first=one.listOperations();await started;const second=two.listOperations();
    one.close();await assert.rejects(first,AbortError);release();assert.deepEqual(await second,[]);
    assert.equal(issuer.seen.filter(item=>item.url.startsWith('/.well-known')).length,1);
    await assert.rejects(one.listOperations(),AbortError);
  }finally{release();one.close();two.close();await issuer.close();}
});

test('pending metadata uses transport identity; completed metadata may cross transports',async()=>{
  clearMetadataCache();let release;const held=new Promise(resolve=>{release=resolve;});let count=0;
  const first={request:async()=>{count++;await held;return Response.json({issuer:'https://identity.example'});}};
  const second={request:async()=>{count++;return Response.json({issuer:'https://identity.example'});}};
  const pending=fetchIssuerMetadata({issuer:'https://identity.example',http:first});
  await fetchIssuerMetadata({issuer:'https://identity.example',http:second});assert.equal(count,2);
  release();await pending;
  await fetchIssuerMetadata({issuer:'https://identity.example',http:{request:async()=>{throw new Error('must use completed value');}}});
  assert.equal(discoveryTransport({transport:first}),first);
  const agent=new HttpAgent();try{assert.equal(discoveryTransport({agents:{http:agent}}),discoveryTransport({agents:{http:agent}}));}finally{agent.destroy();}
});

test('injected relative client still has an SDK operation deadline',async()=>{
  const client=new MemoryClient({timeoutMs:20,client:{request:async()=>new Promise(()=>{})}});
  try{await assert.rejects(client.listOperations(),RequestTimeoutError);}finally{client.close();}
});
