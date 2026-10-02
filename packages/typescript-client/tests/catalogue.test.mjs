/** Check every source-derived tool definition and the base client's validation responsibilities. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {memoryTools,renderToolsList,validateArguments,ToolArgumentError,ToolError,mapError,errorResult,NumericPrecisionError,RequestTimeoutError,AbortError,InputValidationError,MemoryApiError,modelDump} from '../.test-build/internal.js';
const source=JSON.parse(readFileSync(new URL('../contracts/catalogue.json',import.meta.url)));

test('all sixteen definitions preserve metadata, schemas and rendering behavior',()=>{
  const tools=memoryTools();assert.equal(tools.length,16);
  assert.deepEqual(tools.map(t=>({name:t.name,description:t.description,permission:t.permission,tool_version:t.toolVersion,http_route:t.httpRoute,input_schema:t.inputSchema,destructive:t.destructive,annotations:t.annotations})),source.tools);
  const listed=renderToolsList({readOnly:true,project:true});
  assert.equal(listed.length,14);
  for(const entry of listed)assert.deepEqual(entry.inputSchema.properties.project,source.projectArgument);
  const wire=renderToolsList();assert.deepEqual(wire.find(t=>t.name==='ingest').inputSchema,source.ingestWithoutPath);
  wire[0].inputSchema.properties.filename.type='number';assert.equal(memoryTools()[0].inputSchema.properties.filename.type,'string');
});

test('path is refused before any body-mode ambiguity unless a host injects a resolver',async()=>{
  await assert.rejects(validateArguments({name:'ingest',arguments:{path:'/host/file',text:'note',filename:'note.md'}}),error=>error instanceof ToolArgumentError&&error.error.code==='invalid_arguments'&&error.message==='Unknown argument keys: path.');
  let invoked=false;
  const parsed=await validateArguments({name:'ingest',arguments:{path:'/host/file'},pathResolver:async options=>{invoked=true;assert.equal(options.path,'/host/file');return {content:new Uint8Array([1]),filename:'document.bin',mime:'application/octet-stream'};}});
  assert(invoked);assert.deepEqual(Array.from(parsed.content),[1]);
  await assert.rejects(validateArguments({name:'ingest',arguments:{path:'/host/file'},maxBodyBytes:1,pathResolver:async()=>({content:new Uint8Array([1,2]),filename:'document.bin',mime:'application/octet-stream'})}),error=>error.error.code==='body_too_large');
});

test('UTF8, standard base64, lineage and body limits are enforced',async()=>{
  const result=await validateArguments({name:'ingest',arguments:{text:'héllo',filename:'notes.md'}});
  assert.equal(new TextDecoder().decode(result.content),'héllo');assert.equal(result.mime,'text/markdown');assert.equal(result.versioning_mode,'snapshot');
  const binary=await validateArguments({name:'ingest',arguments:{content_base64:'AAEC',filename:'image.png'}});assert.deepEqual(Array.from(binary.content),[0,1,2]);assert.equal(binary.mime,'image/png');
  for(const value of ['data:application/pdf;base64,AA==','A','AA','AA_=', 'AA==\n'])await assert.rejects(validateArguments({name:'ingest',arguments:{content_base64:value,filename:'note.bin'}}),ToolArgumentError);
  await assert.rejects(validateArguments({name:'ingest',arguments:{text:'\ud800',filename:'note.txt'}}),error=>error.error.code==='encoding_error');
  await assert.rejects(validateArguments({name:'ingest',arguments:{content_base64:'====',filename:'note.txt'}}),ToolArgumentError);
  await assert.rejects(validateArguments({name:'ingest',arguments:{text:'note',filename:'note.txt',source_kind:'agent'}}),error=>error.error.code==='source_lineage_pair');
  await assert.rejects(validateArguments({name:'ingest',arguments:{text:'note',filename:'note.txt'},maxBodyBytes:3}),error=>error.error.code==='body_too_large');
});

test('open-query arguments reject explicit nulls and coercion while applying omission defaults',async()=>{
  assert.deepEqual(await validateArguments({name:'describe_query_space',arguments:{}}),{pattern:null,include_examples:false});
  for(const args of [{sql:'SELECT 1',parameters:null},{sql:'SELECT 1',max_rows:true},{sql:'SELECT 1',max_rows:-1}])await assert.rejects(validateArguments({name:'query_sql',arguments:args}),InputValidationError);
  await assert.rejects(validateArguments({name:'describe_query_space',arguments:{pattern:null}}),InputValidationError);
  await assert.rejects(validateArguments({name:'search_query_space',arguments:{query:'x',k:26}}),InputValidationError);
  await assert.rejects(validateArguments({name:'run_saved_query',arguments:{namespace:'bad/path',name:'safe'}}),InputValidationError);
});

test('error mapping preserves timeout hints and distinguishes cancellation and precision',()=>{
  assert.equal(mapError({error:new RequestTimeoutError()}).code,'transport_error');assert.equal(mapError({error:new RequestTimeoutError()}).retryable,true);
  assert.deepEqual([mapError({error:new AbortError()}).code,mapError({error:new AbortError()}).status_code,mapError({error:new AbortError()}).retryable],['cancelled',null,false]);
  const precision=mapError({error:new NumericPrecisionError({detail:'unsafe JSON integer',statusCode:200})});assert.equal(precision.code,'local_backend_error');assert.equal(precision.status_code,null);assert.equal(precision.retryable,false);
  const unexpected=mapError({error:new Error('customer credential fixture')});assert(!JSON.stringify(unexpected.asDict()).includes('customer credential'));
  const toolError=new ToolError({code:'example',detail:'safe',status_code:null,retryable:false,agent_action:'fix'});assert.equal(errorResult({error:toolError}).isError,true);
  assert.equal(mapError({error:new MemoryApiError({statusCode:409,code:'saved_query_revalidation_pending'})}).retryable,true);
});

test('model wire serialization omits recursive defaults and nulls as Python does',()=>{
  assert.deepEqual(modelDump({name:'DocumentSearchRequest',value:{},excludeDefaults:true}),{});
  assert.deepEqual(modelDump({name:'DocumentSearchRequest',value:{filters:{authors:['alice']}},excludeDefaults:true}),{filters:{authors:['alice']}});
  assert.deepEqual(modelDump({name:'SearchRequest',value:{query:'text',documents:{language:'en'}},excludeNone:true}),{query:'text',k:10,channel:'semantic',documents:{family:[],authors:[],recipients:[],language:'en',doc_ids:[]}});
});
