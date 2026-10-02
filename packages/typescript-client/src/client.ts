import { readFile } from 'node:fs/promises';
import { basename } from 'node:path';
import type * as Models from './generated';
import { resolveConnection, EngineRoute, type Connection } from './connection';
import { nodeHttp, discoveryTransport, bounded, checkedResponse, looksMoved, readRoute, transportError, type HttpClient, type HttpTransport, type ClientAgents } from './http';
import { AbortError, InputValidationError, MemoryApiError, RequestTimeoutError, TimeoutError, PipelineDeadLettered, AccountApiUnavailable } from './errors';
import { assertJson } from './json';
import { validateModel, modelDump } from './validation';
import { inferUploadMime } from './mime';
import { uuid, utcTimestamp, validateSavedQueryIdentifier, validateArguments, OPEN_QUERY_TOOL_NAMES } from './catalogue';
import { fetchIssuerMetadata, sendSameOrigin, sameOrigin } from './issuer';

export interface RequestOptions {signal?:AbortSignal;}
export interface ClientOptions {apiKey?:string|null;baseUrl?:string|null;project?:string|null;timeoutMs?:number;client?:HttpClient;transport?:HttpTransport;agents?:ClientAgents;}
export interface IngestOptions extends RequestOptions {source?:Uint8Array|string;content?:Uint8Array;filename?:string;mime?:string;title?:string|null;sourceKind?:string|null;sourceRef?:string|null;sourceModifiedAt?:string|Date|null;versioningMode?:'snapshot'|'living';sourceVersionRef?:string|null;sourcePath?:string|null;}
export type QueryResultDict = Models.OutputQueryResult;
type Envelope = Models.OutputEnvelope;
type ContextBundle = Models.OutputContextBundleV2;
type Params = Record<string,string|number|boolean|null|undefined> | ReadonlyArray<readonly [string,string]>;
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
  for(const [name,value]of entries)if(value!=null)result.append(name,String(value));
  return result;
}
/** Typed asynchronous memory HTTP client; construction performs no network I/O. */
export class MemoryClient {
  protected readonly http:HttpTransport|null;readonly #client:HttpClient|null;readonly #release:(()=>void)|null;protected readonly discovery:HttpTransport; protected readonly connection:Connection|null;
  protected readonly timeoutMs:number; protected readonly controllers=new Set<AbortController>();
  readonly #route:EngineRoute|null; #closed=false;
  /** Resolve settings or accept a caller-owned adapter unchanged. */
  constructor({apiKey,baseUrl,project,timeoutMs=30000,client,transport,agents}:ClientOptions={}) {
    if(!Number.isFinite(timeoutMs)||timeoutMs<=0)throw new InputValidationError({detail:'timeoutMs must be positive and finite'});
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
            const changed=await this.#route.reResolve({signal:activeSignal});
            if(read&&changed)continue;
          }
          throw original;
        }
        if(attempt===0&&this.#route?.keyRouted&&await looksMoved({response})) {
          const changed=await this.#route.reResolve({signal:activeSignal});
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
  async runOperation({name,arguments:args={},signal}:{name:string;arguments?:Record<string,Models.JsonValue>;signal?:AbortSignal}):Promise<Envelope|ContextBundle> {
    const value=await this.json({method:'POST',path:`/operations/${segment({value:name})}`,body:args,signal});
    const contract=(value as {contract?:unknown}|null)?.contract;
    return validateModel({name:contract==='ContextBundle/v2'?'ContextBundleV2':'Envelope',value,response:true});
  }
  /** Execute one sandboxed SQL statement. */
  async querySql({sql,parameters=[],maxRows,signal}:{sql:string;parameters?:Models.JsonValue[];maxRows?:number|null;signal?:AbortSignal}):Promise<QueryResultDict> {
    const body:Record<string,unknown>={sql,parameters};if(maxRows!=null)body.max_rows=maxRows;
    return validateModel({name:'QueryResult',value:await this.json({method:'POST',path:'/query/sql',body,signal}),response:true});
  }
  /** Python-compatible convenience name returning native .rows/.columns/.truncated properties. */
  async openQuery(options:Parameters<MemoryClient['querySql']>[0]):Promise<QueryResultDict> {return this.querySql(options);}
  /** Explain one sandboxed SQL statement without executing it. */
  async explainSql({sql,parameters=[],signal}:{sql:string;parameters?:Models.JsonValue[];signal?:AbortSignal}):Promise<QueryResultDict> {return validateModel({name:'QueryResult',value:await this.json({method:'POST',path:'/query/sql/explain',body:{sql,parameters},signal}),response:true});}
  /** Convenience alias for explainSql. */
  async explainQuery(options:Parameters<MemoryClient['explainSql']>[0]):Promise<QueryResultDict> {return this.explainSql(options);}
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
  async runSavedQuery({namespace,name,parameters=[],version,maxRows,signal}:{namespace:string;name:string;parameters?:Models.JsonValue[];version?:number|null;maxRows?:number|null;signal?:AbortSignal}):Promise<QueryResultDict> {
    clientIdentifier({value:namespace,field:'namespace'});clientIdentifier({value:name,field:'name'});
    const body:Record<string,unknown>={parameters};if(version!=null)body.version=version;if(maxRows!=null)body.max_rows=maxRows;
    return validateModel({name:'QueryResult',value:await this.json({method:'POST',path:`/query/saved/${namespace}/${name}/run`,body,signal}),response:true});
  }
  /** Dispatch the seven open-query infrastructure tools after catalogue validation. */
  async callOpenQuery({name,arguments:args,signal}:{name:string;arguments:Record<string,unknown>;signal?:AbortSignal}):Promise<unknown> {
    if(!OPEN_QUERY_TOOL_NAMES.includes(name))throw new InputValidationError({detail:'unknown open-query tool'});
    const parsed=await validateArguments({name,arguments:args});
    const camel=Object.fromEntries(Object.entries(parsed).map(([key,value])=>[key.replace(/_([a-z])/g,(_,c:string)=>c.toUpperCase()),value]));
    Object.assign(camel,{signal});
    switch(name) {
      case 'query_sql':return this.querySql(camel as Parameters<MemoryClient['querySql']>[0]);
      case 'explain_sql':return this.explainSql(camel as Parameters<MemoryClient['explainSql']>[0]);
      case 'describe_query_space':return this.describeQuerySpace(camel);
      case 'search_query_space':return this.searchQuerySpace(camel as Parameters<MemoryClient['searchQuerySpace']>[0]);
      case 'list_saved_queries':return this.listSavedQueries(camel);
      case 'describe_saved_query':return this.describeSavedQuery(camel as Parameters<MemoryClient['describeSavedQuery']>[0]);
      case 'run_saved_query':return this.runSavedQuery(camel as Parameters<MemoryClient['runSavedQuery']>[0]);
      default:throw new InputValidationError({detail:'unknown open-query tool'});
    }
  }
  /** Run assured facts recall with optional temporal and entity context. */
  async factsContext({query,time,hops,predicate,entityIds,signal}:{query:string;time?:Record<string,Models.JsonValue>|null;hops?:number|null;predicate?:string|null;entityIds?:string[]|null;signal?:AbortSignal}):Promise<Envelope> {
    const args:Record<string,Models.JsonValue>={query};if(time!=null)args.time=time;if(hops!=null)args.hops=hops;if(predicate!=null)args.predicate=predicate;if(entityIds!=null)args.entity_ids=entityIds;
    const value=await this.runOperation({name:'facts_context',arguments:args,signal});return validateModel({name:'Envelope',value,response:true});
  }
  /** Run assured combined fact and source recall. */
  async combinedContext({query,time,signal}:{query:string;time?:Record<string,Models.JsonValue>|null;signal?:AbortSignal}):Promise<ContextBundle> {
    const value=await this.runOperation({name:'combined_context',arguments:{query,...(time!=null?{time}:{})},signal});return validateModel({name:'ContextBundleV2',value,response:true});
  }
  /** Run assured source-claim recall. */
  async claimsAndSourcesContext({query,signal}:{query:string;signal?:AbortSignal}):Promise<Envelope> {return validateModel({name:'Envelope',value:await this.runOperation({name:'claims_and_sources_context',arguments:{query},signal}),response:true});}
  /** Run assured entity resolution. */
  async resolveEntity({name,signal}:{name:string;signal?:AbortSignal}):Promise<Envelope> {return validateModel({name:'Envelope',value:await this.runOperation({name:'resolve_entity',arguments:{name},signal}),response:true});}
  /** Resolve a name with optional repeated focal-entity query parameters. */
  async resolve({name,contextEntityIds=[],signal}:{name:string;contextEntityIds?:string[];signal?:AbortSignal}):Promise<Envelope> {return validateModel({name:'Envelope',value:await this.json({method:'GET',path:'/resolve',params:[['name',name],...contextEntityIds.map(value=>['context_entity_ids',value] as const)],signal}),response:true});}
  /** Read matching current or valid-time relations. */
  async lookupRelations({subjectEntityId,predicate,objectEntityId,validAt,k=50,signal}:{subjectEntityId?:string|null;predicate?:string|null;objectEntityId?:string|null;validAt?:string|Date|null;k?:number;signal?:AbortSignal}={}):Promise<Envelope> {return validateModel({name:'Envelope',value:await this.json({method:'GET',path:'/lookup/relations',params:{k,subject_entity_id:subjectEntityId,predicate,object_entity_id:objectEntityId,valid_at:validAt==null?undefined:utcTimestamp({value:validAt,field:'validAt'})},signal}),response:true});}
  /** Read the bounded decision transcript for a relation. */
  async transcriptRelation({relationId,signal}:{relationId:string;signal?:AbortSignal}):Promise<Envelope> {return validateModel({name:'Envelope',value:await this.json({method:'GET',path:`/transcript/relation/${segment({value:relationId})}`,signal}),response:true});}
  /** Read live property observations for an entity. */
  async lookupObservations({entityId,propertyQuery,k=10,signal}:{entityId:string;propertyQuery?:string|null;k?:number;signal?:AbortSignal}):Promise<Envelope> {return validateModel({name:'Envelope',value:await this.json({method:'GET',path:'/lookup/observations',params:{entity_id:entityId,k,property_query:propertyQuery},signal}),response:true});}
  /** Search source claims; filters switch the wire request from GET to POST. */
  async searchClaims(options:SearchOptions):Promise<Envelope> {return this.search({...options,path:'/search/claims'});}
  /** Search live passages; filters switch the wire request from GET to POST. */
  async searchChunks(options:SearchOptions):Promise<Envelope> {return this.search({...options,path:'/search/chunks'});}
  /** Match the two source search endpoints without duplicating their wire behavior. */
  private async search({query,k=10,channel='semantic',documents,signal,path}:SearchOptions&{path:string}):Promise<Envelope> {
    const value=documents==null?await this.json({method:'GET',path,params:{query,k,channel},signal}):await this.json({method:'POST',path,body:modelDump({name:'SearchRequest',value:{query,k,channel,documents},excludeNone:true}),signal});
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
  async graphNeighborhood({entityId,hops=2,predicates=[],validAt,believedAt,limit=500,continuation=null,includePaths=false,signal}:{entityId:string;hops?:number;predicates?:string[];validAt?:string|Date|null;believedAt?:string|Date|null;limit?:number;continuation?:string|null;includePaths?:boolean;signal?:AbortSignal}):Promise<Envelope> {
    return validateModel({name:'Envelope',value:await this.json({method:'POST',path:'/graph/neighborhood',body:{entity_id:entityId,hops,predicates,valid_at:validAt==null?null:utcTimestamp({value:validAt,field:'validAt'}),believed_at:believedAt==null?null:utcTimestamp({value:believedAt,field:'believedAt'}),limit,continuation,include_paths:includePaths},signal}),response:true});
  }
  /** Read bounded shortest paths between two entities. */
  async graphPath({fromEntityId,toEntityId,maxHops=4,predicates=[],validAt,believedAt,signal}:{fromEntityId:string;toEntityId:string;maxHops?:number;predicates?:string[];validAt?:string|Date|null;believedAt?:string|Date|null;signal?:AbortSignal}):Promise<Envelope> {
    return validateModel({name:'Envelope',value:await this.json({method:'POST',path:'/graph/path',body:{from_entity_id:fromEntityId,to_entity_id:toEntityId,max_hops:maxHops,predicates,valid_at:validAt==null?null:utcTimestamp({value:validAt,field:'validAt'}),believed_at:believedAt==null?null:utcTimestamp({value:believedAt,field:'believedAt'})},signal}),response:true});
  }
  /** Read directed citation paths between documents. */
  async graphCitationPath({fromDocId,toDocId,maxHops=6,signal}:{fromDocId:string;toDocId:string;maxHops?:number;signal?:AbortSignal}):Promise<Envelope> {return validateModel({name:'Envelope',value:await this.json({method:'POST',path:'/graph/citation-path',body:{from_doc_id:fromDocId,to_doc_id:toDocId,max_hops:maxHops},signal}),response:true});}
  /** Read serving code/model bindings before submitting work. */
  async deploymentBuildInfo({signal}:RequestOptions={}):Promise<Models.OutputDeploymentBuildInfo> {return validateModel({name:'DeploymentBuildInfo',value:await this.json({method:'GET',path:'/deployment',signal}),response:true});}
  /** Inspect exact capabilities for a nonempty set of version identifiers. */
  async pipelineReadiness({versionIds,require,signal}:{versionIds:string[];require:Models.ReadinessRequirements;signal?:AbortSignal}):Promise<Models.OutputPipelineReadinessReport> {
    if(!versionIds.length)throw new InputValidationError({detail:'pipeline readiness requires at least one version_id'});
    return validateModel({name:'PipelineReadinessReport',value:await this.json({method:'POST',path:'/readiness',body:{version_ids:versionIds.map(value=>uuid({value,field:'versionId'})),require:modelDump({name:'ReadinessRequirements',value:require})},signal}),response:true});
  }
  /** Poll immediately, stop on dead letters, and bound HTTP/routing/sleep by the readiness deadline. */
  async waitForReadiness({versionIds,timeoutMs=1800000,pollIntervalMs=15000,requireP3=false,signal}:{versionIds:string[];timeoutMs?:number;pollIntervalMs?:number;requireP3?:boolean;signal?:AbortSignal}):Promise<Models.OutputPipelineReadinessReport> {
    if(!Number.isFinite(pollIntervalMs)||pollIntervalMs<=0)throw new InputValidationError({detail:'pollIntervalMs must be positive and finite'});
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
  async ingest({source,content,filename,mime,title,sourceKind,sourceRef,sourceModifiedAt,versioningMode='snapshot',sourceVersionRef,sourcePath,signal}:IngestOptions):Promise<Models.OutputIngestedVersion> {
    if((sourceKind==null)!==(sourceRef==null))throw new InputValidationError({detail:'sourceKind and sourceRef must be supplied together'});
    if(sourceKind==null&&(sourceModifiedAt!=null||sourceVersionRef!=null||versioningMode!=='snapshot'))throw new InputValidationError({detail:'source timestamps, revisions, and living mode require sourceKind/sourceRef'});
    let bytes=content;
    if(bytes!==undefined&&source!==undefined&&!filename)filename=typeof source==='string'?source:undefined;
    else if(bytes===undefined&&typeof source==='string') {bytes=await readFile(source);filename=filename||basename(source);mime=mime||inferUploadMime({filename:source});}
    else if(bytes===undefined&&source instanceof Uint8Array)bytes=source;
    if(!(bytes instanceof Uint8Array)||!filename)throw new InputValidationError({detail:'content and filename are required when ingesting bytes'});
    const params={filename,mime:mime||inferUploadMime({filename}),versioning_mode:versioningMode,title,source_kind:sourceKind,source_ref:sourceRef,source_modified_at:sourceModifiedAt==null?undefined:utcTimestamp({value:sourceModifiedAt,field:'sourceModifiedAt'}),source_version_ref:sourceVersionRef,source_path:sourcePath};
    return validateModel({name:'IngestedVersion',value:await this.json({method:'POST',path:'/ingest',params,content:bytes,headers:{'Content-Type':'application/octet-stream'},signal}),response:true});
  }
  /** Read a page of the deployment's documents. */
  async listDocuments({limit=50,cursor,status,signal}:{limit?:number;cursor?:string|null;status?:Models.DocumentStatusFilter|null;signal?:AbortSignal}={}):Promise<Models.OutputDocumentPage> {return validateModel({name:'DocumentPage',value:await this.json({method:'GET',path:'/documents',params:{limit,cursor,status},signal}),response:true});}
  /** Search documents by name, metadata and content with source-owned defaults. */
  async searchDocuments({query=null,filters={},versions='current',k=20,cursor=null,signal}:{query?:string|null;filters?:Models.DocumentSearchFilters;versions?:'current'|'all';k?:number;cursor?:string|null;signal?:AbortSignal}={}):Promise<Models.OutputDocumentSearchPage> {return this.searchDocumentsRequest({request:{query,filters,versions,k,cursor},signal});}
  /** Send a prepared document search, excluding recursively default-valued fields. */
  async searchDocumentsRequest({request,signal}:{request:Models.DocumentSearchRequest;signal?:AbortSignal}):Promise<Models.OutputDocumentSearchPage> {return validateModel({name:'DocumentSearchPage',value:await this.json({method:'POST',path:'/documents/search',body:modelDump({name:'DocumentSearchRequest',value:request,excludeDefaults:true}),signal}),response:true});}
  /** Delete one live document after locally validating its identity. */
  async deleteDocument({docId,signal}:{docId:string;signal?:AbortSignal}):Promise<Models.OutputDocumentDeletion> {return validateModel({name:'DocumentDeletion',value:await this.json({method:'DELETE',path:`/documents/${uuid({value:docId,field:'docId'})}`,signal}),response:true});}
  /** List deployment-side connectors; never execute their adapters in this client. */
  async connectors({signal}:RequestOptions={}):Promise<Models.OutputConnectorDescriptor[]> {return listResponse({name:'ConnectorDescriptor',value:await this.json({method:'GET',path:'/connectors',signal})});}
  /** Create deployment-side connector configuration using credential references. */
  async addConnector({connector,signal}:{connector:Models.ConnectorCreate;signal?:AbortSignal}):Promise<Models.OutputConnectorDescriptor> {return validateModel({name:'ConnectorDescriptor',value:await this.json({method:'POST',path:'/connectors',body:modelDump({name:'ConnectorCreate',value:connector}),signal}),response:true});}
  /** Pause a deployment-side connector. */
  async pauseConnector({connectorId,signal}:{connectorId:string;signal?:AbortSignal}):Promise<Models.OutputConnectorDescriptor> {return validateModel({name:'ConnectorDescriptor',value:await this.json({method:'POST',path:`/connectors/${segment({value:connectorId})}/pause`,signal}),response:true});}
  /** Read one deployment-side connector's status. */
  async connectorStatus({connectorId,signal}:{connectorId:string;signal?:AbortSignal}):Promise<Models.OutputConnectorDescriptor> {return validateModel({name:'ConnectorDescriptor',value:await this.json({method:'GET',path:`/connectors/${segment({value:connectorId})}`,signal}),response:true});}
}
export interface SearchOptions extends RequestOptions {query:string;k?:number;channel?:'semantic'|'bm25';documents?:Models.DocumentSearchFilters|null;}
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
/** Issuer account requests are separate from the memory engine API. */
export class AccountApi {
  readonly #connection:Connection|null;readonly #http:HttpTransport;readonly #timeoutMs:number;readonly #controllers:Set<AbortController>;readonly #assertOpen:()=>void;
  /** Bind the account transport without fetching issuer metadata. */
  constructor({connection,http,timeoutMs=30000,controllers=new Set<AbortController>(),assertOpen=()=>{}}:{connection:Connection|null;http:HttpTransport;timeoutMs?:number;controllers?:Set<AbortController>;assertOpen?:()=>void}) {this.#assertOpen=assertOpen;this.#connection=connection;this.#http=http;this.#timeoutMs=timeoutMs;this.#controllers=controllers;}
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
    const c=this.#connection;
    if(!c?.claims||!c.authorization)throw new AccountApiUnavailable({detail:'connection has no issuer account API'});
    return bounded({signal,timeoutMs:this.#timeoutMs,controllers:this.#controllers,work:async activeSignal=>{
      let endpoint:string;
      try {endpoint=(await fetchIssuerMetadata({issuer:c.claims!.iss,http:this.#http,signal:activeSignal,timeoutMs:this.#timeoutMs})).endpoint({name:'remember_account_endpoint'});}
      catch(error) {if(error instanceof AbortError||error instanceof RequestTimeoutError)throw error;throw new AccountApiUnavailable({detail:'issuer has no usable account API'});}
      if(!sameOrigin({left:c.claims!.iss,right:endpoint}))throw new AccountApiUnavailable({detail:'account endpoint must share the issuer origin'});
      const response=await sendSameOrigin({http:this.#http,request:{method:'GET',url:endpoint.replace(/\/+$/,'')+'/'+path.replace(/^\/+/,''),query:queryParams({params}),headers:{Authorization:c.authorization!,Accept:'application/json'},signal:activeSignal}});
      return await checkedResponse({response,path}) as Models.JsonValue;
    }});
  }
}
export {Client as RememberClient};
