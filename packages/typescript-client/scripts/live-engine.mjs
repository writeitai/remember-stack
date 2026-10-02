/** Exercise every served client method against the actual released/candidate engine. */
import assert from 'node:assert/strict';
import {Agent} from 'node:http';
import {readFileSync,mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Client,MemoryApiError,TimeoutError,PipelineDeadLettered} from '../dist/index.js';
const profile=JSON.parse(readFileSync(process.argv[2],'utf8'));
const version=process.env.TARGET_ENGINE_VERSION;
const fixtures=JSON.parse(readFileSync(new URL('../tests/fixtures/python-methods.json',import.meta.url)));
const routes=JSON.parse(readFileSync(new URL('../contracts/routes.json',import.meta.url)));
const directory=mkdtempSync(join(tmpdir(),'remember-live-'));const file=join(directory,'probe.md');writeFileSync(file,'# SDK probe\nLocal compatibility fixture.\n');
process.env.REMEMBER_CONFIG_DIR=directory;
for(const name of ['REMEMBER_API_URL','REMEMBER_API_KEY','REMEMBER_PROJECT','REMEMBER_ISSUER'])delete process.env[name];
const observed=[];
/** Observe the SDK's actual native requests without substituting its transport. */
class RecordingAgent extends Agent {
  /** Record the method and path before Node sends the request; never record credentials.
   * @param {import('node:http').ClientRequest} request
   * @param {import('node:http').RequestOptions} options
   * @returns {void}
   */
  addRequest(request,options){observed.push({method:request.method,path:request.path.split('?')[0]});super.addRequest(request,options);}
}
const agent=new RecordingAgent({keepAlive:true});
const client=Client.fromEnv({agents:{http:agent},apiKey:'test-compatibility-key',baseUrl:'http://127.0.0.1:8000',timeoutMs:30000});
/** Match the exact route template and HTTP method in the composed profile. */
function served({request}) {
  return Object.entries(profile.paths).some(([path,methods])=>new RegExp('^'+path.replace(/[.*+?^${}()|[\]\\]/g,'\\$&').replace(/\\\{[^}]+\\\}/g,'[^/]+')+'$').test(request.path)&&methods[request.method.toLowerCase()]);
}
const outcomes=[];
try {
  if(version==='candidate')for(const route of Object.values(routes).filter(route=>!route.optionalProfile))assert(profile.paths[route.path]?.[route.method.toLowerCase()],`candidate profile lost ${route.method} ${route.path}`);
  const ops=await client.listOperations();assert(ops.length>0,'deployment must serve assured descriptors');
  const ingest=await client.ingest({source:Buffer.from('# Probe\nSDK parity fixture.\n'),filename:'typescript-probe.md'});
  const temporal=[];
  const id='10000000-0000-0000-0000-000000000001';
  for(const [method,base]of [['lookupRelations',{}],['graphNeighborhood',{entityId:id}],['graphPath',{fromEntityId:id,toEntityId:id}]])for(const validAt of ['2026-10-02T12:00:00Z','2026-10-02T12:00:00+02:00']) {
    const utc=validAt.endsWith('Z');
    try{
      await client[method]({...base,validAt});
      assert(version!=='candidate'||utc,`${method} candidate accepted a non-UTC clock`);
      temporal.push({method,validAt,outcome:'served-success'});
    }catch(error){
      if(version==='candidate'&&!utc){assert(error instanceof MemoryApiError&&error.statusCode===422&&error.detail.includes('UTC'),`${method} must report the candidate UTC boundary refusal: ${error}`);temporal.push({method,validAt,outcome:'utc-boundary-422'});}
      else {assert(error instanceof MemoryApiError&&[400,404,409].includes(error.statusCode),`${method} clock failed before its served business operation: ${error}`);temporal.push({method,validAt,outcome:`served-business-refusal-${error.statusCode}`});}
    }
  }
  const selected=fixtures.cases.filter(item=>item.variant==='defaults'||item.method==='call_open_query'||item.typescriptMethod==='ingestFile'||(['searchClaims','searchChunks'].includes(item.typescriptMethod)&&item.wire[0].method==='POST'));
  const methods=new Set();
  for(const scenario of selected) {
    methods.add(scenario.typescriptMethod);
    const options=structuredClone(scenario.typescriptOptions);
    for(const key of ['source','filePath'])if(options[key]==='$FILE')options[key]=file;
    if(options.content?.base64)options.content=Buffer.from(options.content.base64,'base64');
    if(options.channel)options.channel='bm25';
    if(['searchClaims','searchChunks'].includes(scenario.typescriptMethod))options.channel='bm25';
    if(options.versionIds)options.versionIds=[ingest.version_id];
    if(scenario.typescriptMethod==='pipelineReadiness')options.require={pipeline:true,p1:false,live_graph:false,p3:false};
    if(scenario.typescriptMethod==='waitForReadiness'){options.timeoutMs=500;options.pollIntervalMs=50;}
    const start=observed.length;
    const exists=scenario.wire.every(request=>served({request}));
    try {
      await client[scenario.typescriptMethod](options);
      assert(exists,`${scenario.typescriptMethod} fabricated success for an absent profile route`);
      outcomes.push({method:scenario.typescriptMethod,outcome:'served-success'});
    }catch(error) {
      if(!exists){assert(error instanceof MemoryApiError&&[404,405].includes(error.statusCode),`absent ${scenario.typescriptMethod} must refuse as unsupported: ${error}`);outcomes.push({method:scenario.typescriptMethod,outcome:'explicit-unsupported'});continue;}
      if(scenario.typescriptMethod==='waitForReadiness'&&(error instanceof TimeoutError||error instanceof PipelineDeadLettered)){assert(error.report,'readiness failure must retain the last real report');outcomes.push({method:scenario.typescriptMethod,outcome:'real-readiness-pending'});continue;}
      // Real fixture identifiers/registry entries need not exist. A typed business
      // refusal still exercises the served endpoint; transport/decoder/5xx failures do not.
      assert(error instanceof MemoryApiError&&[400,404,409,422].includes(error.statusCode),`${scenario.typescriptMethod} unexpected failure: ${error.stack}`);
      if(scenario.wire[0].path.startsWith('/query/'))assert(error.code,`query refusal lacks its bound structured code: ${error.detail}`);
      outcomes.push({method:scenario.typescriptMethod,outcome:`served-business-refusal-${error.statusCode}`});
    }finally {
      const actual=observed.slice(start);assert(actual.length>0,`${scenario.typescriptMethod} sent no real request`);
      for(const request of actual)assert(scenario.wire.some(expected=>request.method===expected.method&&request.path===expected.path),`${scenario.typescriptMethod} sent an unexpected ${request.method} ${request.path}`);
    }
  }
  const covered=new Set(fixtures.cases.map(item=>item.typescriptMethod));
  for(const name of covered)assert(methods.has(name),`${name} lacks a live deployment disposition`);
  const deletionRoute={method:'DELETE',path:`/documents/${ingest.doc_id}`};
  if(served({request:deletionRoute}))await client.deleteDocument({docId:ingest.doc_id});
  console.log(JSON.stringify({engine:version,outcomes,temporal},null,2));
}finally{client.close();agent.destroy();rmSync(directory,{recursive:true,force:true});}
