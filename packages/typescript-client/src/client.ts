import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { basename } from 'node:path';
import type * as Models from './generated';
import { resolveConnection, EngineRoute, type Connection } from './connection';
import { nodeHttp, discoveryTransport, bounded, checkedResponse, checkedAccountResponse, looksMoved, readRoute, transportError, type HttpClient, type HttpTransport, type ClientAgents } from './http';
import { AbortError, InputValidationError, MemoryApiError, RequestTimeoutError, TimeoutError, PipelineDeadLettered, AccountApiUnavailable, UnknownTag } from './errors';
import { assertJson } from './json';
import { validateModel, modelDump } from './validation';
import { inferUploadMime } from './mime';
import { utcTimestamp } from './timestamps';
import { uuid, validateSavedQueryIdentifier, validateArguments, OPEN_QUERY_TOOL_NAMES } from './catalogue';
import { fetchIssuerMetadata, sendSameOrigin, sameOrigin } from './issuer';

export interface RequestOptions {signal?:AbortSignal;}
export interface ClientOptions {apiKey?:string|null;baseUrl?:string|null;project?:string|null;timeoutMs?:number;client?:HttpClient;transport?:HttpTransport;agents?:ClientAgents;}
export interface IngestOptions extends RequestOptions {source?:Uint8Array|string;content?:Uint8Array;filename?:string;mime?:string;title?:string|null;sourceKind?:string|null;sourceRef?:string|null;sourceModifiedAt?:string|Date|null;versioningMode?:'snapshot'|'living';sourceVersionRef?:string|null;sourcePath?:string|null;versionKey?:string|null;effectiveFrom?:string|Date|null;effectiveUntil?:string|Date|null;tags?:readonly string[]|null;allowForgotten?:boolean;}
export type QueryResultDict = Models.OutputQueryResult;
type Envelope = Models.OutputEnvelope;
type ContextBundle = Models.OutputContextBundleV2;
type Params = Record<string,string|number|boolean|null|undefined> | ReadonlyArray<readonly [string,string]>;
/** Render a D143 layer selection (any of sources, claims, facts; omitted means every served layer) as repeated layers query parameters. */
function layerParams({layers}:{layers?:readonly string[]|null}):Params|undefined {
  if(layers==null)return undefined;
  // Omitting layers reads every served layer; an empty selection is not that request, so it is refused rather than widened.
  if(typeof layers==='string'||layers.length===0)throw new InputValidationError({detail:'layers must name at least one of sources, claims, facts; omit it to read every layer the deployment serves'});
  return layers.map(layer=>['layers',String(layer)] as const);
}
/** Python's tag-name rule (D156): NFC, 1-128 characters, no whitespace or control characters. */
function tagName({value}:{value:string}):string {
  if(typeof value!=='string')throw new InputValidationError({detail:'a tag name must be a string'});
  const normalized=value.normalize('NFC');const length=Array.from(normalized).length;
  if(length<1||length>128)throw new InputValidationError({detail:'a tag name is 1 to 128 characters'});
  if(/[\p{White_Space}\p{Cc}]/u.test(normalized))throw new InputValidationError({detail:'a tag name has no whitespace or control characters'});
  return normalized;
}
/** Python's tag-reference rule (D156): at most 512 characters as sent and 1-128 after NFC. */
function tagRef({value}:{value:string}):string {
  if(Array.from(value).length>512||!(Array.from(value.normalize('NFC')).length>=1&&Array.from(value.normalize('NFC')).length<=128))throw new InputValidationError({detail:'a tag reference is 1 to 128 characters after NFC'});
  return value;
}
/** A sequence of tag names or ids; one string is refused rather than split. */
function tagList({value,field}:{value:readonly string[]|null|undefined;field:string}):string[] {
  if(value==null)return [];
  if(typeof value==='string'||!Array.isArray(value))throw new InputValidationError({detail:`${field} is a sequence of tag names or ids, not one string`});
  return value.map(tag=>String(tag));
}
/** Encode one identifier as a URL segment, including characters encodeURIComponent leaves literal. */
function segment({value}:{value:string}):string {return encodeURIComponent(value).replace(/[!'()*]/g,c=>`%${c.charCodeAt(0).toString(16).toUpperCase()}`);}
/** Adapt direct SDK identifier failures to Python's ordinary ValueError behavior. */
function clientIdentifier({value,field}:{value:unknown;field:string}):string {
  try{return validateSavedQueryIdentifier({value,field});}
  catch(error){if(error instanceof InputValidationError)throw new InputValidationError({detail:error.message});throw error;}
}
/** Require a JSON object in discovery/account APIs with intentionally open response fields. */
function objectResponse({value}:{value:unknown}):Record<string,Models.JsonValue> {
  if(value===null||typeof value!=='object'||Array.isArray(value))throw new MemoryApiError({statusCode:200,detail:'response must be a JSON object'});
  return value as Record<string,Models.JsonValue>;
}
/** Validate all list members before exposing any to the caller. */
function listResponse<T>({name,value}:{name:string;value:unknown}):T[] {
  if(!Array.isArray(value))throw new MemoryApiError({statusCode:200,detail:'response must be a JSON array'});
  return value.map(item=>validateModel<T>({name,value:item,response:true}));
}
/** Convert optional named values to wire query parameters without literal nulls. */
function queryParams({params}:{params?:Params}):URLSearchParams|undefined {
  if(!params)return undefined;
  const result=new URLSearchParams();
  const entries=Array.isArray(params)?params:Object.entries(params);
  for(const [name,value]of entries)if(value!=null){if(typeof value==='number')assertJson({value});result.append(name,String(value));}
  return result;
}
/** Typed asynchronous memory HTTP client; construction performs no network I/O. */
export class MemoryClient {
  protected readonly http:HttpTransport|null;readonly #client:HttpClient|null;readonly #release:(()=>void)|null;protected readonly discovery:HttpTransport; protected readonly connection:Connection|null;
  protected readonly timeoutMs:number; protected readonly controllers=new Set<AbortController>();
  readonly #route:EngineRoute|null; #closed=false;
  /** Resolve settings or accept a caller-owned adapter unchanged. */
  constructor({apiKey,baseUrl,project,timeoutMs=300000,client,transport,agents}:ClientOptions={}) {
    if(!Number.isFinite(timeoutMs)||timeoutMs<=0||timeoutMs>2147483647)throw new InputValidationError({detail:'timeoutMs must be positive, finite and at most 2147483647'});
    this.timeoutMs=timeoutMs;
    if(client) {
      if([apiKey,baseUrl,project,transport,agents].some(value=>value!=null))throw new InputValidationError({detail:'an injected client cannot be combined with client settings'});
      this.#client=client;this.http=null;this.#release=null;this.discovery=discoveryTransport();this.connection=null;this.#route=null;
    }else {
      if(transport&&agents)throw new InputValidationError({detail:'transport and agents are mutually exclusive'});
      const owned=transport?null:nodeHttp({agents});
      this.http=transport??owned!.transport;this.#client=null;this.#release=owned?.close??null;
      this.discovery=discoveryTransport({transport,agents});
      this.connection=resolveConnection({apiKey,apiUrl:baseUrl,project});
      this.#route=new EngineRoute({connection:this.connection,http:this.discovery,timeoutMs});
    }
  }
  /** Refuse future operations after closing, including previously obtained account facades. */
  protected assertOpen():void {if(this.#closed)throw new AbortError();}
  /** Cancel this client's in-flight operations without closing a caller-owned adapter. */
  close():void {if(this.#closed)return;this.#closed=true;this.#release?.();for(const controller of this.controllers)controller.abort(new AbortError());}
  /** Support JavaScript explicit resource management. */
  [Symbol.dispose]():void {this.close();}
  /** Support asynchronous explicit resource management. */
  async [Symbol.asyncDispose]():Promise<void> {this.close();}
  /** Bound routing, transmission and body consumption with one operation deadline. */
  protected async json({method,path,params,body,content,headers,signal,timeoutMs=this.timeoutMs}:{method:string;path:string;params?:Params;body?:unknown;content?:Uint8Array;headers?:Record<string,string>;signal?:AbortSignal;timeoutMs?:number}):Promise<unknown> {
    this.assertOpen();
    if(body!==undefined)assertJson({value:body});
    const query=queryParams({params});const merged={Accept:'application/json',...headers};
    const payload=content!==undefined?new Uint8Array(content):body!==undefined?JSON.stringify(body):undefined;
    if(body!==undefined)Object.assign(merged,{'Content-Type':'application/json'});
    return bounded({signal,timeoutMs,controllers:this.controllers,work:async activeSignal=>{
      const read=readRoute({method,path});
      for(let attempt=0;attempt<2;attempt++) {
        const target=await this.#route?.target({signal:activeSignal});
        const requestHeaders={...merged,...(target?.authorization?{Authorization:target.authorization}:{})};
        let response:Response;
        try {
          response=target
            ?await this.http!.request({method,url:target.url.replace(/\/+$/,'')+path,query,headers:requestHeaders,body:payload,signal:activeSignal})
            :await this.#client!.request({method,path,query,headers:requestHeaders,body:payload,signal:activeSignal});
        }catch(error) {
          if(activeSignal.aborted)throw activeSignal.reason;
          if(error instanceof AbortError||error instanceof RequestTimeoutError)throw error;
          const original=transportError({error});
          if(original instanceof AbortError||original instanceof RequestTimeoutError)throw original;
          if(attempt===0&&original.statusCode===0&&this.#route?.keyRouted) {
            const changed=await this.#route.reResolve({signal:activeSignal,previousUrl:target!.url});
            if(read&&changed)continue;
          }
          throw original;
        }
        if(attempt===0&&this.#route?.keyRouted&&await looksMoved({response})) {
          const changed=await this.#route.reResolve({signal:activeSignal,previousUrl:target!.url});
          if(read&&changed) {await response.body?.cancel();continue;}
        }
        return checkedResponse({response,path});
      }
      throw new Error('unreachable request state');
    }});
  }
  /** Return every assured-operation descriptor served by the deployment. */
  async listOperations({signal}:RequestOptions={}):Promise<Models.OutputToolDescriptor[]> {return listResponse({name:'ToolDescriptor',value:await this.json({method:'GET',path:'/operations',signal})});}
  /** Run an assured operation, discriminating the exact public response contract. */
  async runOperation({name,arguments:args={},layers,signal}:{name:string;arguments?:Record<string,Models.JsonValue>;layers?:readonly string[]|null;signal?:AbortSignal}):Promise<Envelope|ContextBundle> {
    const value=await this.json({method:'POST',path:`/operations/${segment({value:name})}`,params:layerParams({layers}),body:args,signal});
    const contract=(value as {contract?:unknown}|null)?.contract;
    return validateModel({name:contract==='ContextBundle/v2'?'ContextBundleV2':'Envelope',value,response:true});
  }
  /** Execute one sandboxed SQL statement. */
  async querySql({sql,parameters=[],maxRows,layers,signal}:{sql:string;parameters?:Models.JsonValue[];maxRows?:number|null;layers?:readonly string[]|null;signal?:AbortSignal}):Promise<QueryResultDict> {
    const body:Record<string,unknown>={sql,parameters};if(maxRows!=null)body.max_rows=maxRows;
    return validateModel({name:'QueryResult',value:await this.json({method:'POST',path:'/query/sql',params:layerParams({layers}),body,signal}),response:true});
  }
  /** Python-compatible convenience name returning native .rows/.columns/.truncated properties. */
  async openQuery({sql,parameters,maxRows,signal}:{sql:string;parameters?:Models.JsonValue[];maxRows?:number|null;signal?:AbortSignal}):Promise<QueryResultDict> {return this.querySql({sql,parameters,maxRows,signal});}
  /** Explain one sandboxed SQL statement without executing it. */
  async explainSql({sql,parameters=[],layers,signal}:{sql:string;parameters?:Models.JsonValue[];layers?:readonly string[]|null;signal?:AbortSignal}):Promise<QueryResultDict> {return validateModel({name:'QueryResult',value:await this.json({method:'POST',path:'/query/sql/explain',params:layerParams({layers}),body:{sql,parameters},signal}),response:true});}
  /** Convenience alias for explainSql. */
  async explainQuery({sql,parameters,signal}:{sql:string;parameters?:Models.JsonValue[];signal?:AbortSignal}):Promise<QueryResultDict> {return this.explainSql({sql,parameters,signal});}
  /** Discover manifest-backed schema objects and optional examples. */
  async describeQuerySpace({pattern,includeExamples=false,signal}:{pattern?:string|null;includeExamples?:boolean;signal?:AbortSignal}={}):Promise<Record<string,Models.JsonValue>> {return objectResponse({value:await this.json({method:'GET',path:'/query/space',params:{include_examples:includeExamples,pattern},signal})});}
  /** Search manifest text with atomic typed result validation. */
  async searchQuerySpace({query,k=10,signal}:{query:string;k?:number;signal?:AbortSignal}):Promise<Models.OutputDiscoveryHit[]> {return listResponse({name:'DiscoveryHit',value:await this.json({method:'GET',path:'/query/space/search',params:{query,k},signal})});}
  /** List saved-query registry metadata. */
  async listSavedQueries({namespace,status,signal}:{namespace?:string|null;status?:string|null;signal?:AbortSignal}={}):Promise<Models.OutputSavedQuerySummary[]> {return listResponse({name:'SavedQuerySummary',value:await this.json({method:'GET',path:'/query/saved',params:{namespace,status},signal})});}
  /** Describe one safe saved-query identifier and optional version. */
  async describeSavedQuery({namespace,name,version,signal}:{namespace:string;name:string;version?:number|null;signal?:AbortSignal}):Promise<Record<string,Models.JsonValue>> {
    clientIdentifier({value:namespace,field:'namespace'});clientIdentifier({value:name,field:'name'});
    return objectResponse({value:await this.json({method:'GET',path:`/query/saved/${namespace}/${name}`,params:{version},signal})});
  }
  /** Execute one active saved-query version through the sandbox. */
  async runSavedQuery({namespace,name,parameters=[],version,maxRows,layers,signal}:{namespace:string;name:string;parameters?:Models.JsonValue[];version?:number|null;maxRows?:number|null;layers?:readonly string[]|null;signal?:AbortSignal}):Promise<QueryResultDict> {
    clientIdentifier({value:namespace,field:'namespace'});clientIdentifier({value:name,field:'name'});
    const body:Record<string,unknown>={parameters};if(version!=null)body.version=version;if(maxRows!=null)body.max_rows=maxRows;
    return validateModel({name:'QueryResult',value:await this.json({method:'POST',path:`/query/saved/${namespace}/${name}/run`,params:layerParams({layers}),body,signal}),response:true});
  }
  /** Dispatch the seven open-query infrastructure tools after catalogue validation; layers narrows executing tools' reads. */
  async callOpenQuery({name,arguments:args,layers,signal}:{name:string;arguments:Record<string,unknown>;layers?:readonly string[]|null;signal?:AbortSignal}):Promise<unknown> {
    if(!OPEN_QUERY_TOOL_NAMES.includes(name))throw new InputValidationError({detail:'unknown open-query tool'});
    const parsed=await validateArguments({name,arguments:args});
    const camel=Object.fromEntries(Object.entries(parsed).map(([key,value])=>[key.replace(/_([a-z])/g,(_,c:string)=>c.toUpperCase()),value]));
    Object.assign(camel,{signal});
    const executing={...camel,layers};
    switch(name) {
      case 'query_sql':return this.querySql(executing as Parameters<MemoryClient['querySql']>[0]);
      case 'explain_sql':return this.explainSql(executing as Parameters<MemoryClient['explainSql']>[0]);
      case 'describe_query_space':return this.describeQuerySpace(camel);
      case 'search_query_space':return this.searchQuerySpace(camel as Parameters<MemoryClient['searchQuerySpace']>[0]);
      case 'list_saved_queries':return this.listSavedQueries(camel);
      case 'describe_saved_query':return this.describeSavedQuery(camel as Parameters<MemoryClient['describeSavedQuery']>[0]);
      case 'run_saved_query':return this.runSavedQuery(executing as Parameters<MemoryClient['runSavedQuery']>[0]);
      default:throw new InputValidationError({detail:'unknown open-query tool'});
    }
  }
  /** Run assured facts recall with optional temporal, entity and document (D156) context. */
  async factsContext({query,time,hops,predicate,entityIds,k,candidateK,documents,signal}:{query:string;time?:Record<string,Models.JsonValue>|null;hops?:number|null;predicate?:string|null;entityIds?:string[]|null;k?:number;candidateK?:number;documents?:Models.DocumentSearchFilters|null;signal?:AbortSignal}):Promise<Envelope> {
    const args:Record<string,Models.JsonValue>={query};if(time!=null)args.time=time;if(hops!=null)args.hops=hops;if(predicate!=null)args.predicate=predicate;if(entityIds!=null)args.entity_ids=entityIds;if(k!=null)args.k=k;if(candidateK!=null)args.candidate_k=candidateK;if(documents!=null)args.documents=documentsArgument({documents});
    const value=await this.runOperation({name:'facts_context',arguments:args,signal});return validateModel({name:'Envelope',value,response:true});
  }
  /** Run assured combined fact and source recall; a side with none of its layers admitted returns empty with a boundary negative. */
  async combinedContext({query,time,k,candidateK,layers,documents,signal}:{query:string;time?:Record<string,Models.JsonValue>|null;k?:number;candidateK?:number;layers?:readonly string[]|null;documents?:Models.DocumentSearchFilters|null;signal?:AbortSignal}):Promise<ContextBundle> {
    const value=await this.runOperation({name:'combined_context',arguments:{query,...(time!=null?{time}:{}),...(k!=null?{k}:{}),...(candidateK!=null?{candidate_k:candidateK}:{}),...(documents!=null?{documents:documentsArgument({documents})}:{})},layers,signal});return validateModel({name:'ContextBundleV2',value,response:true});
  }
  /** Run assured source-claim recall; layers ['sources'] answers from source passages only. */
  async claimsAndSourcesContext({query,time,k,candidateK,layers,documents,signal}:{query:string;time?:Record<string,Models.JsonValue>|null;k?:number;candidateK?:number;layers?:readonly string[]|null;documents?:Models.DocumentSearchFilters|null;signal?:AbortSignal}):Promise<Envelope> {return validateModel({name:'Envelope',value:await this.runOperation({name:'claims_and_sources_context',arguments:{query,...(time!=null?{time}:{}),...(k!=null?{k}:{}),...(candidateK!=null?{candidate_k:candidateK}:{}),...(documents!=null?{documents:documentsArgument({documents})}:{})},layers,signal}),response:true});}
  /** Run assured entity resolution. */
  async resolveEntity({name,signal}:{name:string;signal?:AbortSignal}):Promise<Envelope> {return validateModel({name:'Envelope',value:await this.runOperation({name:'resolve_entity',arguments:{name},signal}),response:true});}
  /** Resolve a name with optional repeated focal-entity query parameters. */
  async resolve({name,contextEntityIds=[],signal}:{name:string;contextEntityIds?:string[];signal?:AbortSignal}):Promise<Envelope> {return validateModel({name:'Envelope',value:await this.json({method:'GET',path:'/resolve',params:[['name',name],...contextEntityIds.map(value=>['context_entity_ids',value] as const)],signal}),response:true});}
  /** Read matching current or valid-time relations. */
  async lookupRelations({subjectEntityId,predicate,objectEntityId,validAt,limit=50,cursor,signal}:{subjectEntityId?:string|null;predicate?:string|null;objectEntityId?:string|null;validAt?:string|Date|null;limit?:number;cursor?:string|null;signal?:AbortSignal}={}):Promise<Envelope> {return validateModel({name:'Envelope',value:await this.json({method:'GET',path:'/lookup/relations',params:{limit,cursor,subject_entity_id:subjectEntityId,predicate,object_entity_id:objectEntityId,valid_at:validAt==null?undefined:utcTimestamp({value:validAt,field:'validAt'})},signal}),response:true});}
  /** Read the bounded decision transcript for a relation. */
  async transcriptRelation({relationId,signal}:{relationId:string;signal?:AbortSignal}):Promise<Envelope> {return validateModel({name:'Envelope',value:await this.json({method:'GET',path:`/transcript/relation/${segment({value:relationId})}`,signal}),response:true});}
  /** Read live property observations for an entity. */
  async lookupObservations({entityId,propertyQuery,k=10,limit=50,cursor,signal}:{entityId:string;propertyQuery?:string|null;k?:number;limit?:number;cursor?:string|null;signal?:AbortSignal}):Promise<Envelope> {
    if(propertyQuery!=null&&cursor!=null)throw new InputValidationError({detail:'cursor pages filter-only observation lookups; drop propertyQuery or cursor'});
    const params=propertyQuery==null?{entity_id:entityId,limit,cursor}:{entity_id:entityId,property_query:propertyQuery,k};
    return validateModel({name:'Envelope',value:await this.json({method:'GET',path:'/lookup/observations',params,signal}),response:true});
  }
  /** Search source claims; filters switch the wire request from GET to POST. */
  async searchClaims(options:SearchOptions):Promise<Envelope> {return this.search({...options,path:'/search/claims'});}
  /** Search live passages; filters switch the wire request from GET to POST. */
  async searchChunks(options:SearchOptions):Promise<Envelope> {return this.search({...options,path:'/search/chunks'});}
  /** Match the two source search endpoints without duplicating their wire behavior. */
  private async search({query,k=10,channel='semantic',documents,time,signal,path}:SearchOptions&{path:string}):Promise<Envelope> {
    const value=documents==null&&time==null?await this.json({method:'GET',path,params:{query,k,channel},signal}):await this.json({method:'POST',path,body:modelDump({name:'SearchRequest',value:{query,k,channel,documents:documents??null,time:time??null},excludeNone:true}),signal});
    return validateModel({name:'Envelope',value,response:true});
  }
  /** Fetch one or two surrounding chunks in document order. */
  async adjacentChunks({chunkId,window=1,signal}:{chunkId:string;window?:number;signal?:AbortSignal}):Promise<Envelope> {
    const id=uuid({value:chunkId,field:'chunkId'});if(!Number.isInteger(window)||window<1||window>2)throw new InputValidationError({detail:'window must be between 1 and 2'});
    return validateModel({name:'Envelope',value:await this.json({method:'GET',path:`/chunks/${id}/adjacent`,params:{window},signal}),response:true});
  }
  /** Hydrate a relation through its supporting evidence and source records. */
  async hydrateRelation({relationId,signal}:{relationId:string;signal?:AbortSignal}):Promise<Envelope> {return validateModel({name:'Envelope',value:await this.json({method:'GET',path:`/hydrate/relation/${segment({value:relationId})}`,signal}),response:true});}
  /** Read a bounded current or bitemporal graph neighborhood. */
  async graphNeighborhood({entityId,hops=2,predicates=[],validAt,believedAt,limit=100,cursor=null,includePaths=false,signal}:{entityId:string;hops?:number;predicates?:string[];validAt?:string|Date|null;believedAt?:string|Date|null;limit?:number;cursor?:string|null;includePaths?:boolean;signal?:AbortSignal}):Promise<Envelope> {
    return validateModel({name:'Envelope',value:await this.json({method:'POST',path:'/graph/neighborhood',body:{entity_id:entityId,hops,predicates,valid_at:validAt==null?null:utcTimestamp({value:validAt,field:'validAt'}),believed_at:believedAt==null?null:utcTimestamp({value:believedAt,field:'believedAt'}),limit,cursor,include_paths:includePaths},signal}),response:true});
  }
  /** Read bounded shortest paths between two entities. */
  async graphPath({fromEntityId,toEntityId,maxHops=4,predicates=[],validAt,believedAt,signal}:{fromEntityId:string;toEntityId:string;maxHops?:number;predicates?:string[];validAt?:string|Date|null;believedAt?:string|Date|null;signal?:AbortSignal}):Promise<Envelope> {
    return validateModel({name:'Envelope',value:await this.json({method:'POST',path:'/graph/path',body:{from_entity_id:fromEntityId,to_entity_id:toEntityId,max_hops:maxHops,predicates,valid_at:validAt==null?null:utcTimestamp({value:validAt,field:'validAt'}),believed_at:believedAt==null?null:utcTimestamp({value:believedAt,field:'believedAt'})},signal}),response:true});
  }
  /** Read directed citation paths between documents. */
  async graphCitationPath({fromDocId,toDocId,maxHops=6,signal}:{fromDocId:string;toDocId:string;maxHops?:number;signal?:AbortSignal}):Promise<Envelope> {return validateModel({name:'Envelope',value:await this.json({method:'POST',path:'/graph/citation-path',body:{from_doc_id:fromDocId,to_doc_id:toDocId,max_hops:maxHops},signal}),response:true});}
  /** Read serving code/model bindings before submitting work. */
  async deploymentBuildInfo({signal}:RequestOptions={}):Promise<Models.OutputDeploymentBuildInfo> {return validateModel({name:'DeploymentBuildInfo',value:await this.json({method:'GET',path:'/deployment',signal}),response:true});}
  /** Read which switchable processing layers (claims, facts) are enabled, backfilling or disabled. */
  async processingLayers({signal}:RequestOptions={}):Promise<Models.OutputProcessingLayerStates> {return validateModel({name:'ProcessingLayerStates',value:await this.json({method:'GET',path:'/processing-layers',signal}),response:true});}
  /** Inspect exact capabilities for a nonempty set of version identifiers. */
  async pipelineReadiness({versionIds,require,signal}:{versionIds:string[];require:Models.ReadinessRequirements;signal?:AbortSignal}):Promise<Models.OutputPipelineReadinessReport> {
    if(!versionIds.length)throw new InputValidationError({detail:'pipeline readiness requires at least one version_id'});
    return validateModel({name:'PipelineReadinessReport',value:await this.json({method:'POST',path:'/readiness',body:{version_ids:versionIds.map(value=>uuid({value,field:'versionId'})),require:modelDump({name:'ReadinessRequirements',value:require})},signal}),response:true});
  }
  /** Poll immediately, stop on dead letters, and bound HTTP/routing/sleep by the readiness deadline. */
  async waitForReadiness({versionIds,timeoutMs=1800000,pollIntervalMs=15000,requireP3=false,signal}:{versionIds:string[];timeoutMs?:number;pollIntervalMs?:number;requireP3?:boolean;signal?:AbortSignal}):Promise<Models.OutputPipelineReadinessReport> {
    if(!Number.isFinite(pollIntervalMs)||pollIntervalMs<=0||pollIntervalMs>2147483647)throw new InputValidationError({detail:'pollIntervalMs must be positive, finite and at most 2147483647'});
    let report:Models.OutputPipelineReadinessReport|undefined;
    let readinessSignal:AbortSignal|undefined;
    try {return await bounded({signal,timeoutMs,controllers:this.controllers,work:async activeSignal=>{
      readinessSignal=activeSignal;
      while(true) {
        report=await this.pipelineReadiness({versionIds,require:{pipeline:true,p1:true,live_graph:true,p3:requireP3},signal:activeSignal});
        if(report.ready)return report;
        const deadLettered=report.versions.flatMap(version=>version.stages.filter(stage=>stage.status==='dead_letter').map(stage=>[version.version_id,stage.stage,stage.status] as const));
        if(deadLettered.length)throw new PipelineDeadLettered({deadLettered,report});
        await sleep({milliseconds:pollIntervalMs,signal:activeSignal});
      }
    }});}catch(error) {if(error instanceof RequestTimeoutError&&readinessSignal?.aborted&&readinessSignal.reason===error)throw new TimeoutError({report});throw error;}
  }
  /** Ingest bytes or a file path with optional stable document lineage. */
  async ingest(options:IngestOptions):Promise<Models.OutputIngestedVersion> {
    this.assertOpen();
    return bounded({signal:options.signal,timeoutMs:this.timeoutMs,controllers:this.controllers,work:signal=>this.#ingest({...options,signal})});
  }
  /** Include local file loading in the ingest operation's cancellation and deadline. */
  async #ingest({source,content,filename,mime,title,sourceKind,sourceRef,sourceModifiedAt,versioningMode='snapshot',sourceVersionRef,sourcePath,versionKey,effectiveFrom,effectiveUntil,tags,allowForgotten=false,signal}:IngestOptions):Promise<Models.OutputIngestedVersion> {
    const tagParams=tagList({value:tags,field:'tags'}).map(tag=>['tags',tag] as const);
    if(typeof allowForgotten!=='boolean')throw new InputValidationError({detail:'allowForgotten must be a boolean'});
    if((sourceKind==null)!==(sourceRef==null))throw new InputValidationError({detail:'sourceKind and sourceRef must be supplied together'});
    if(sourceKind==null&&(sourceModifiedAt!=null||sourceVersionRef!=null||versioningMode!=='snapshot'||versionKey!=null||effectiveFrom!=null||effectiveUntil!=null))throw new InputValidationError({detail:'source timestamps, revisions, and living mode require sourceKind/sourceRef'});
    if(effectiveUntil!=null&&effectiveFrom==null)throw new InputValidationError({detail:'effectiveUntil requires effectiveFrom'});
    const start=effectiveFrom==null?undefined:utcTimestamp({value:effectiveFrom,field:'effectiveFrom'});
    const end=effectiveUntil==null?undefined:utcTimestamp({value:effectiveUntil,field:'effectiveUntil'});
    if(start!==undefined) {
      if(versioningMode!=='snapshot')throw new InputValidationError({detail:'effective periods require snapshot mode'});
      validateModel({name:'EffectivePeriodInput',value:{effective_from:start,effective_until:end??null}});
    }
    let bytes=content;
    if(bytes!==undefined&&source!==undefined&&!filename)filename=typeof source==='string'?source:undefined;
    else if(bytes===undefined&&typeof source==='string') {bytes=await readFile(source,{signal});filename=filename||basename(source);mime=mime||inferUploadMime({filename:source});}
    else if(bytes===undefined&&source instanceof Uint8Array)bytes=source;
    if(!(bytes instanceof Uint8Array)||!filename)throw new InputValidationError({detail:'content and filename are required when ingesting bytes'});
    const params={filename,mime:mime||inferUploadMime({filename}),versioning_mode:versioningMode,title,source_kind:sourceKind,source_ref:sourceRef,source_modified_at:sourceModifiedAt==null?undefined:utcTimestamp({value:sourceModifiedAt,field:'sourceModifiedAt'}),source_version_ref:sourceVersionRef,source_path:sourcePath,version_key:versionKey,effective_from:start,effective_until:end};
    const ordered=[...Object.entries(params).filter((entry):entry is [string,string]=>entry[1]!=null),...tagParams,...(allowForgotten?[['allow_forgotten','true'] as const]:[])];
    return validateModel({name:'IngestedVersion',value:await this.json({method:'POST',path:'/ingest',params:ordered,content:bytes,headers:{'Content-Type':'application/octet-stream'},signal}),response:true});
  }
  /** Read a page of the deployment's documents; archived ones only with includeArchived. */
  async listDocuments({limit=50,cursor,status,includeArchived=false,signal}:{limit?:number;cursor?:string|null;status?:Models.DocumentStatusFilter|null;includeArchived?:boolean;signal?:AbortSignal}={}):Promise<Models.OutputDocumentPage> {return validateModel({name:'DocumentPage',value:await this.json({method:'GET',path:'/documents',params:{limit,cursor,status,include_archived:includeArchived?'true':null},signal}),response:true});}
  /** Search documents by name, metadata and content with source-owned defaults. */
  async searchDocuments({query=null,filters={},versions='current',k=20,limit=50,cursor=null,time=null,signal}:{query?:string|null;filters?:Models.DocumentSearchFilters;versions?:'current'|'all';k?:number;limit?:number;cursor?:string|null;time?:Models.ReadTime|null;signal?:AbortSignal}={}):Promise<Models.OutputDocumentSearchPage> {return this.searchDocumentsRequest({request:{query,filters,versions,k,limit,cursor,time},signal});}
  /** Send a prepared document search, excluding recursively default-valued fields. */
  async searchDocumentsRequest({request,signal}:{request:Models.DocumentSearchRequest;signal?:AbortSignal}):Promise<Models.OutputDocumentSearchPage> {
    const body={...modelDump({name:'DocumentSearchRequest',value:request,excludeDefaults:true})};
    if(request.time!=null)body.time=modelDump({name:'ReadTime',value:request.time});
    return validateModel({name:'DocumentSearchPage',value:await this.json({method:'POST',path:'/documents/search',body,signal}),response:true});
  }
  /** Follow a keyed section across versions, defaulting to its complete history. */
  async sectionHistory({docId,sectionKey,time=null,limit=50,cursor=null,signal}:{docId:string;sectionKey:string;time?:Models.ReadTime|null;limit?:number;cursor?:string|null;signal?:AbortSignal}):Promise<Models.OutputSectionHistoryPage> {
    return this.sectionHistoryRequest({request:{doc_id:uuid({value:docId,field:'docId'}),section_key:sectionKey,time:time??{mode:'history'},limit,cursor},signal});
  }
  /** Encode a prepared section-history request with UTC bounds. */
  async sectionHistoryRequest({request,signal}:{request:Models.SectionHistoryRequest;signal?:AbortSignal}):Promise<Models.OutputSectionHistoryPage> {
    const input=validateModel<Models.OutputSectionHistoryRequest>({name:'SectionHistoryRequest',value:request});
    const params:Record<string,string|number|null>={mode:input.time.mode,limit:input.limit};
    if(input.time.mode==='at')params.at=input.time.at;
    else if(input.time.mode==='overlap'){params.from=input.time.from;params.to=input.time.to;}
    if(input.cursor!=null)params.cursor=input.cursor;
    return validateModel({name:'SectionHistoryPage',value:await this.json({method:'GET',path:`/documents/${input.doc_id}/sections/${segment({value:input.section_key})}/history`,params,signal}),response:true});
  }
  /** Replace one version's complete supplied reference set as source-ordered NDJSON. */
  async setReferences({docId,versionId,references,signal}:{docId:string;versionId:string;references:readonly Models.ReferenceInput[];signal?:AbortSignal}):Promise<Models.OutputReferencesSet> {
    const document=uuid({value:docId,field:'docId'}),version=uuid({value:versionId,field:'versionId'});
    const content=new TextEncoder().encode(references.map(reference=>JSON.stringify(modelDump({name:'ReferenceInput',value:reference,excludeNone:true}))+'\n').join(''));
    return validateModel({name:'ReferencesSet',value:await this.json({method:'PUT',path:`/documents/${document}/versions/${version}/references`,content,headers:{'Content-Type':'application/x-ndjson'},signal}),response:true});
  }
  /** Read the pending, active, rejected and superseded reference generations. */
  async referenceGenerations({docId,versionId,signal}:{docId:string;versionId:string;signal?:AbortSignal}):Promise<Models.OutputReferenceGenerations> {
    return validateModel({name:'ReferenceGenerations',value:await this.json({method:'GET',path:`/documents/${uuid({value:docId,field:'docId'})}/versions/${uuid({value:versionId,field:'versionId'})}/references`,signal}),response:true});
  }
  /** Follow incoming/outgoing references from exactly one document or chunk. */
  async documentReferences({chunkId=null,docId=null,sectionKey=null,direction='both',kinds=null,time=null,limit=50,cursor=null,signal}:{chunkId?:string|null;docId?:string|null;sectionKey?:string|null;direction?:'outgoing'|'incoming'|'both';kinds?:Models.ReferenceKind[]|null;time?:Models.ReadTime|null;limit?:number;cursor?:string|null;signal?:AbortSignal}={}):Promise<Models.OutputDocumentReferencesPage> {
    return this.documentReferencesRequest({request:{chunk_id:chunkId==null?null:uuid({value:chunkId,field:'chunkId'}),doc_id:docId==null?null:uuid({value:docId,field:'docId'}),section_key:sectionKey,direction,kinds,time,limit,cursor},signal});
  }
  /** Send a prepared references request with canonical aliases and omitted nulls. */
  async documentReferencesRequest({request,signal}:{request:Models.DocumentReferencesRequest;signal?:AbortSignal}):Promise<Models.OutputDocumentReferencesPage> {
    return validateModel({name:'DocumentReferencesPage',value:await this.json({method:'POST',path:'/documents/references',body:modelDump({name:'DocumentReferencesRequest',value:request,excludeNone:true}),signal}),response:true});
  }
  /** Replace one version's complete effective-period declarations atomically. */
  async setEffectivePeriods({docId,versionId,periods,signal}:{docId:string;versionId:string;periods:readonly Models.EffectivePeriodInput[];signal?:AbortSignal}):Promise<Models.OutputEffectivePeriodsSet> {
    return validateModel({name:'EffectivePeriodsSet',value:await this.json({method:'PUT',path:`/documents/${uuid({value:docId,field:'docId'})}/versions/${uuid({value:versionId,field:'versionId'})}/effective-periods`,body:modelDump({name:'EffectivePeriodsRequest',value:{periods}}),signal}),response:true});
  }
  /** Clear declared effective time without replaying an ambiguous write. */
  async clearEffectiveTime({docId,signal}:{docId:string;signal?:AbortSignal}):Promise<Models.OutputEffectiveTimeCleared> {
    return validateModel({name:'EffectiveTimeCleared',value:await this.json({method:'DELETE',path:`/documents/${uuid({value:docId,field:'docId'})}/effective-periods`,signal}),response:true});
  }
  /** Archive one document: the memory stops believing it, but keeps it readable as history (D159). */
  async archiveDocument({docId,signal}:{docId:string;signal?:AbortSignal}):Promise<Models.OutputDocumentArchive> {return validateModel({name:'DocumentArchive',value:await this.json({method:'POST',path:`/documents/${uuid({value:docId,field:'docId'})}/archive`,signal}),response:true});}
  /** Undo an archive exactly: the document's testimony is current again (D159). */
  async unarchiveDocument({docId,signal}:{docId:string;signal?:AbortSignal}):Promise<Models.OutputDocumentUnarchive> {return validateModel({name:'DocumentUnarchive',value:await this.json({method:'POST',path:`/documents/${uuid({value:docId,field:'docId'})}/unarchive`,signal}),response:true});}
  /** Forget one document for good: it leaves every read at once and what derives only from it is erased (D159). */
  async forgetDocument({docId,signal}:{docId:string;signal?:AbortSignal}):Promise<Models.OutputForget> {return validateModel({name:'Forget',value:await this.json({method:'POST',path:`/documents/${uuid({value:docId,field:'docId'})}/forget`,signal}),response:true});}
  /** List the deployment's forget records, oldest first (D159). */
  async listForgets({signal}:{signal?:AbortSignal}={}):Promise<Models.OutputForgetList> {return validateModel({name:'ForgetList',value:await this.json({method:'GET',path:'/forgets',signal}),response:true});}
  /** Read one forget record and its progress (D159). */
  async getForget({forgetId,signal}:{forgetId:string;signal?:AbortSignal}):Promise<Models.OutputForget> {return validateModel({name:'Forget',value:await this.json({method:'GET',path:`/forgets/${uuid({value:forgetId,field:'forgetId'})}`,signal}),response:true});}
  /** List the deployment's registered tags (D156), optionally narrowed by a name or description substring. */
  async listTags({query=null,signal}:{query?:string|null;signal?:AbortSignal}={}):Promise<Models.OutputTagList> {return validateModel({name:'TagList',value:await this.json({method:'GET',path:'/tags',params:query==null?undefined:{query},signal}),response:true});}
  /** Register a tag before documents carry it; a name taken in any letter case is a 409 conflict. */
  async createTag({name,description,signal}:{name:string;description:string;signal?:AbortSignal}):Promise<Models.OutputTag> {
    const body=modelDump({name:'TagCreate',value:{name:tagName({value:name}),description}});
    return validateModel({name:'Tag',value:await this.json({method:'POST',path:'/tags',body,signal}),response:true});
  }
  /** Rename or re-describe a tag given by name or tag_id; documents keep it. */
  async updateTag({tag,name=null,description=null,signal}:{tag:string;name?:string|null;description?:string|null;signal?:AbortSignal}):Promise<Models.OutputTag> {
    const body=modelDump({name:'TagUpdate',value:{name:name==null?null:tagName({value:name}),description},excludeNone:true});
    const tagId=await this.#tagId({tag,signal});
    return validateModel({name:'Tag',value:await this.json({method:'PATCH',path:`/tags/${tagId}`,body,signal}),response:true});
  }
  /** Delete a tag given by name or tag_id, with every assignment of it. */
  async deleteTag({tag,signal}:{tag:string;signal?:AbortSignal}):Promise<Models.OutputTag> {
    const tagId=await this.#tagId({tag,signal});
    return validateModel({name:'Tag',value:await this.json({method:'DELETE',path:`/tags/${tagId}`,signal}),response:true});
  }
  /** Add and remove, or replace with set, one document's tags atomically. */
  async updateDocumentTags({docId,add=null,remove=null,set=null,signal}:{docId:string;add?:readonly string[]|null;remove?:readonly string[]|null;set?:readonly string[]|null;signal?:AbortSignal}):Promise<Models.OutputDocumentTags> {
    const document=uuid({value:docId,field:'docId'});
    const refs=({value,field}:{value:readonly string[]|null|undefined;field:string}):string[]=>tagList({value,field}).map(tag=>tagRef({value:tag}));
    const body=modelDump({name:'DocumentTagsUpdate',value:{add:refs({value:add,field:'add'}),remove:refs({value:remove,field:'remove'}),set:set==null?null:refs({value:set,field:'set'})},excludeDefaults:true});
    return validateModel({name:'DocumentTags',value:await this.json({method:'POST',path:`/documents/${document}/tags`,body,signal}),response:true});
  }
  /** Resolve a tag name to its id through the engine's exact-name match; an id is used as given. */
  async #tagId({tag,signal}:{tag:string;signal?:AbortSignal}):Promise<string> {
    try{return uuid({value:tag,field:'tag'});}catch(error){if(!(error instanceof InputValidationError))throw error;}
    // The engine matches the name exactly as the registry does (NFC, ignoring case); comparing case here could disagree with it.
    const matches=validateModel<Models.OutputTagList>({name:'TagList',value:await this.json({method:'GET',path:'/tags',params:{name:tag},signal}),response:true}).tags;
    if(matches.length!==1)throw new UnknownTag({tags:[tag],statusCode:404});
    return matches[0]!.tag_id;
  }
  /** List deployment-side connectors; never execute their adapters in this client. */
  async connectors({signal}:RequestOptions={}):Promise<Models.OutputConnectorDescriptor[]> {return listResponse({name:'ConnectorDescriptor',value:await this.json({method:'GET',path:'/connectors',signal})});}
  /** Create deployment-side connector configuration using credential references. */
  async addConnector({connector,signal}:{connector:Models.ConnectorCreate;signal?:AbortSignal}):Promise<Models.OutputConnectorDescriptor> {return validateModel({name:'ConnectorDescriptor',value:await this.json({method:'POST',path:'/connectors',body:modelDump({name:'ConnectorCreate',value:connector}),signal}),response:true});}
  /** Pause a deployment-side connector. */
  async pauseConnector({connectorId,signal}:{connectorId:string;signal?:AbortSignal}):Promise<Models.OutputConnectorDescriptor> {return validateModel({name:'ConnectorDescriptor',value:await this.json({method:'POST',path:`/connectors/${segment({value:connectorId})}/pause`,signal}),response:true});}
  /** Read one deployment-side connector's status. */
  async connectorStatus({connectorId,signal}:{connectorId:string;signal?:AbortSignal}):Promise<Models.OutputConnectorDescriptor> {return validateModel({name:'ConnectorDescriptor',value:await this.json({method:'GET',path:`/connectors/${segment({value:connectorId})}`,signal}),response:true});}
}
/** The JSON ``documents`` operation argument (D134/D156), validated and default-free. */
function documentsArgument({documents}:{documents:Models.DocumentSearchFilters}):Models.JsonValue {return modelDump({name:'DocumentSearchFilters',value:documents,excludeDefaults:true}) as Models.JsonValue;}
export interface SearchOptions extends RequestOptions {query:string;k?:number;channel?:'semantic'|'bm25';documents?:Models.DocumentSearchFilters|null;time?:Models.ReadTime|null;}
/** Wait without leaving a timer or listener behind after cancellation. */
async function sleep({milliseconds,signal}:{milliseconds:number;signal:AbortSignal}):Promise<void> {
  if(signal.aborted)throw signal.reason;
  await new Promise<void>((resolve,reject)=>{
    /** Complete the wait and release its abort listener. */
    const complete=():void=>{signal.removeEventListener('abort',cancel);resolve();};
    /** Cancel the timer while preserving the operation's cancellation identity. */
    const cancel=():void=>{clearTimeout(timer);signal.removeEventListener('abort',cancel);reject(signal.reason);};
    const timer=setTimeout(complete,milliseconds);signal.addEventListener('abort',cancel,{once:true});
  });
}
/** Memory client plus issuer account access and named file ingest convenience. */
export class Client extends MemoryClient {
  /** Apply the same explicit/environment/file precedence as the ordinary constructor. */
  static fromEnv(options:ClientOptions={}):Client {return new this(options);}
  /** Access the issuer account facade lazily, with no construction-time network calls. */
  get account():AccountApi {return new AccountApi({connection:this.connection,http:this.discovery,timeoutMs:this.timeoutMs,controllers:this.controllers,assertOpen:()=>this.assertOpen()});}
  /** Infer MIME from the actual path before applying a display filename override. */
  async ingestFile({filePath,...options}:Omit<IngestOptions,'source'|'content'>&{filePath:string}):Promise<Models.OutputIngestedVersion> {return this.ingest({...options,source:filePath});}
}

type AccountObject = Record<string, Models.JsonValue>;

/** Hosted payment details for a project that needs human action. */
export interface ProjectActivationCheckout extends AccountObject {
  checkout_session_id: string;
  url: string;
  amount_credits: string;
  client_secret: string | null;
}

/** Issuer JSON from creating a project or paying for activation. */
export type ProjectActivationResult = AccountObject & {
  project: AccountObject;
  payment: 'credits' | 'saved_card' | 'checkout';
  requires_human_action: boolean;
  checkout?: ProjectActivationCheckout | null;
};

type AccountRequest = RequestOptions & {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE';
  path: string;
  params?: Record<string, string | number>;
  body?: AccountObject;
  idempotencyKey?: string;
};

/** Encode an opaque account ID as a single non-traversing path segment. */
function accountId(value: string): string {
  if (typeof value !== 'string' || !value || value === '.' || value === '..' || /[/\\%]/.test(value)) {
    throw new InputValidationError({detail: 'an account id must be a single path segment'});
  }
  return segment({value});
}

/** Use the caller's retry key or generate a fresh key for this POST. */
function projectIdempotencyKey(value: string | null | undefined): string {
  if (value == null) return randomUUID();
  if (typeof value !== 'string' || !value || value !== value.trim() || value.length > 160 || /[^\x20-\x7e]/.test(value)) {
    throw new InputValidationError({detail: 'Idempotency-Key must be 1 to 160 printable ASCII characters with no surrounding spaces'});
  }
  return value;
}

/** Issuer account requests are separate from the memory engine API. */
export class AccountApi {
  readonly #connection:Connection|null;readonly #http:HttpTransport;readonly #timeoutMs:number;readonly #controllers:Set<AbortController>;readonly #assertOpen:()=>void;
  /** Bind the account transport without fetching issuer metadata. */
  constructor({connection,http,timeoutMs=300000,controllers=new Set<AbortController>(),assertOpen=()=>{}}:{connection:Connection|null;http:HttpTransport;timeoutMs?:number;controllers?:Set<AbortController>;assertOpen?:()=>void}) {this.#assertOpen=assertOpen;this.#connection=connection;this.#http=http;this.#timeoutMs=timeoutMs;this.#controllers=controllers;}
  /** Read whoami as an object from the issuer account endpoint. */
  async whoami({signal}:RequestOptions={}):Promise<Record<string,Models.JsonValue>> {return objectResponse({value:await this.get({path:'/v1/keys/self',signal})});}
  /** Read a safe relative account path; reject traversal and cross-origin redirects. */
  async get({path,params,signal}:{path:string;params?:Record<string,string|number>;signal?:AbortSignal}):Promise<Models.JsonValue> {
    this.#assertOpen();
    if(typeof path!=='string'||!path||path.startsWith('//')||path.includes('\\')||/[?#]/.test(path))throw new InputValidationError({detail:'account path must be a relative path without traversal'});
    let decoded=path;
    for(let layer=0;layer<=path.length;layer++) {
      if(decoded.startsWith('//')||decoded.includes('\\')||decoded.split('/').some(part=>part==='.'||part==='..')||/^[a-z][a-z0-9+.-]*:/i.test(decoded))throw new InputValidationError({detail:'account path must be a relative path without traversal'});
      let next:string;try {next=decodeURIComponent(decoded);}catch {throw new InputValidationError({detail:'account path has invalid percent encoding'});}
      if(next===decoded)break;decoded=next;
    }
    return this.#request({method: 'GET', path, params, signal});
  }

  /** List the key's visible projects and the organisation's slot usage. */
  async listProjects({signal}: RequestOptions = {}): Promise<AccountObject> {
    return this.#object({method: 'GET', path: this.#orgPath('projects'), signal});
  }

  /** Read a covered project's cap, activation and deployment state. */
  async getProject({projectId, signal}: RequestOptions & {projectId: string}): Promise<AccountObject> {
    return this.#object({method: 'GET', path: this.#projectPath(projectId), signal});
  }

  /** Create a project and return its activation payment outcome. */
  async createProject({name, monthlySubcapCredits = null, idempotencyKey, signal}: RequestOptions & {
    name: string;
    monthlySubcapCredits?: string | null;
    idempotencyKey?: string | null;
  }): Promise<ProjectActivationResult> {
    return this.#object({
      method: 'POST', path: this.#orgPath('projects'), signal,
      body: {name, monthly_subcap_credits: monthlySubcapCredits},
      idempotencyKey: projectIdempotencyKey(idempotencyKey),
    }) as Promise<ProjectActivationResult>;
  }

  /** Set a project's monthly cap in credits, or pass null to clear it. */
  async setProjectSpendCap({projectId, monthlySubcapCredits, signal}: RequestOptions & {
    projectId: string;
    monthlySubcapCredits: string | null;
  }): Promise<AccountObject> {
    return this.#object({
      method: 'PUT', path: this.#projectPath(projectId) + '/spend-cap', signal,
      body: {monthly_subcap_credits: monthlySubcapCredits},
    });
  }

  /** Pay for an unpaid project's activation or return a hosted Checkout. */
  async startActivationCheckout({projectId, idempotencyKey, signal}: RequestOptions & {
    projectId: string;
    idempotencyKey?: string | null;
  }): Promise<ProjectActivationResult> {
    return this.#object({
      method: 'POST', path: this.#projectPath(projectId) + '/activation-checkout',
      idempotencyKey: projectIdempotencyKey(idempotencyKey), signal,
    }) as Promise<ProjectActivationResult>;
  }

  /** List key metadata visible to the caller, without the secrets. */
  async listKeys({signal}: RequestOptions = {}): Promise<AccountObject | Models.JsonValue[]> {
    const value = await this.#request({method: 'GET', path: this.#orgPath('keys'), signal});
    return Array.isArray(value) ? value : objectResponse({value});
  }

  /** Create a child key with the given coverage and return its one-time secret. */
  async createKey({name, projects, permissions, defaultProjectId, lifetimeDays, signal}: RequestOptions & {
    name: string;
    projects?: readonly string[] | 'org:*' | null;
    permissions?: readonly string[] | null;
    defaultProjectId?: string | null;
    lifetimeDays?: number | null;
  }): Promise<AccountObject> {
    if (projects != null && projects !== 'org:*' && !Array.isArray(projects)) {
      throw new InputValidationError({detail: 'projects must be a sequence of project ids or "org:*"'});
    }
    if (permissions != null && !Array.isArray(permissions)) {
      throw new InputValidationError({detail: 'permissions must be a sequence of permission names'});
    }
    const body: AccountObject = {name};
    if (projects != null) body.projects = projects === 'org:*' ? projects : [...projects];
    if (permissions != null) body.permissions = [...permissions];
    if (defaultProjectId != null) body.default_project_id = defaultProjectId;
    if (lifetimeDays != null) body.lifetime_days = lifetimeDays;
    return this.#object({method: 'POST', path: this.#orgPath('keys'), body, signal});
  }

  /** Revoke a key and any keys that descend from it. */
  async revokeKey({keyId, signal}: RequestOptions & {keyId: string}): Promise<AccountObject> {
    return this.#object({method: 'DELETE', path: `${this.#orgPath('keys')}/${accountId(keyId)}`, signal});
  }

  /** Address one project in the signed key's organisation. */
  #projectPath(projectId: string): string {
    return `${this.#orgPath('projects')}/${accountId(projectId)}`;
  }

  /** Use the organisation claim for routing. The issuer authorizes the call. */
  #orgPath(resource: 'projects' | 'keys'): string {
    this.#assertOpen();
    const organisation = this.#connection?.claims?.org;
    if (!organisation) throw new AccountApiUnavailable({detail: 'this account call needs an organisation-bound signed key'});
    return `/v1/orgs/${accountId(organisation)}/${resource}`;
  }

  /** Check that the account endpoint returned a JSON object. */
  async #object(request: AccountRequest): Promise<AccountObject> {
    return objectResponse({value: await this.#request(request)});
  }

  /** Send the request only to the signed key's issuer account endpoint. */
  async #request({method, path, params, body, idempotencyKey, signal}: AccountRequest): Promise<Models.JsonValue> {
    this.#assertOpen();
    const c=this.#connection;
    if(!c?.claims||!c.authorization)throw new AccountApiUnavailable({detail:'connection has no issuer account API'});
    const authorization = c.authorization;
    return bounded({signal,timeoutMs:this.#timeoutMs,controllers:this.#controllers,work:async activeSignal=>{
      let endpoint:string;
      const metadata=await fetchIssuerMetadata({issuer:c.claims!.iss,http:this.#http,signal:activeSignal,timeoutMs:this.#timeoutMs});
      try {endpoint=metadata.endpoint({name:'remember_account_endpoint'});}
      catch(error) {if(error instanceof AbortError||error instanceof RequestTimeoutError)throw error;throw new AccountApiUnavailable({detail:'issuer has no usable account API'});}
      if(!sameOrigin({left:c.claims!.iss,right:endpoint}))throw new AccountApiUnavailable({detail:'account endpoint must share the issuer origin'});
      const headers: Record<string, string> = {Authorization: authorization, Accept: 'application/json'};
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      if (idempotencyKey !== undefined) headers['Idempotency-Key'] = idempotencyKey;
      let response:Response;
      try {
        response = await sendSameOrigin({http: this.#http, request: {
          method, url: endpoint.replace(/\/+$/,'')+'/'+path.replace(/^\/+/,''),
          query: queryParams({params}), headers, body: JSON.stringify(body), signal: activeSignal,
        }});
      }
      catch(error){if(activeSignal.aborted)throw activeSignal.reason;throw transportError({error});}
      return await checkedAccountResponse({response, authorization}) as Models.JsonValue;
    }});
  }
}
export {Client as RememberClient};
