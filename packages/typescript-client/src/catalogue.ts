import {queryArgumentError} from './query-arguments';
import catalogue from './catalogue.generated';
import { InputValidationError } from './errors';
import { assertJson } from './json';
import { validateModel, validateSchema } from './validation';
import { inferUploadMime } from './mime';
import { ToolError, ToolArgumentError, invalidArguments } from './tool-errors';
export type Permission = 'memory:read' | 'memory:write';
export const INGEST_TOOL_NAME='ingest';
export const PIPELINE_READINESS_TOOL_NAME='pipeline_readiness';
export const DELETE_DOCUMENT_TOOL_NAME='delete_document';
export const SEARCH_DOCUMENTS_TOOL_NAME='search_documents';
export const ADJACENT_CHUNKS_TOOL_NAME='adjacent_chunks';
export const SECTION_HISTORY_TOOL_NAME='section_history';
export const DOCUMENT_REFERENCES_TOOL_NAME='document_references';
export const OPEN_QUERY_TOOL_NAMES=Object.freeze(['query_sql','explain_sql','describe_query_space','search_query_space','list_saved_queries','describe_saved_query','run_saved_query']);
export const OPERATION_TOOL_NAMES=Object.freeze(['resolve_entity','claims_and_sources_context','facts_context','combined_context']);
export const MEMORY_WRITE_TOOL_NAMES=Object.freeze([INGEST_TOOL_NAME,PIPELINE_READINESS_TOOL_NAME]);
export const PROJECT_ARGUMENT=structuredClone(catalogue.projectArgument);
/** A source-derived tool contract, independent of any MCP host implementation. */
export class ToolDefinition {
  readonly name:string; readonly description:string; readonly permission:Permission; readonly toolVersion:number;
  readonly httpRoute:string; readonly inputSchema:Record<string,unknown>; readonly destructive:boolean;
  /** Preserve all catalogue metadata without duplicating its source schema. */
  constructor({definition}:{definition:typeof catalogue.tools[number]}) {
    this.name=definition.name;this.description=definition.description;this.permission=definition.permission as Permission;
    this.toolVersion=definition.tool_version;this.httpRoute=definition.http_route;
    this.inputSchema=structuredClone(definition.input_schema);this.destructive=definition.destructive;
  }
  /** Determine whether a host needs write permission. */
  get mutates():boolean {return this.permission==='memory:write';}
  /** Source-compatible MCP annotations. */
  get annotations():Record<string,boolean> {return {readOnlyHint:!this.mutates,destructiveHint:this.destructive};}
}
const definitions=catalogue.tools.map(definition=>new ToolDefinition({definition}));
/** Return every memory-tool definition in the source-defined order. */
export function memoryTools():readonly ToolDefinition[] {return definitions.map(definition=>new ToolDefinition({definition:catalogue.tools.find(item=>item.name===definition.name)!}));}
/** Look up a tool definition; unknown names are local argument errors. */
export function tool({name}:{name:string}):ToolDefinition {
  const definition=catalogue.tools.find(item=>item.name===name);
  if(!definition) throw new InputValidationError({detail:'not a memory tool'});
  return new ToolDefinition({definition});
}
/** Render host-selected tools without mutating the catalogue or performing host I/O. */
export function renderToolsList({tools=memoryTools(),project=false,pathIngest=false,readOnly=false}:{tools?:Iterable<ToolDefinition>;project?:boolean;pathIngest?:boolean;readOnly?:boolean}={}):Record<string,unknown>[] {
  const result:Record<string,unknown>[]=[];
  for(const definition of tools) {
    if(readOnly&&definition.mutates)continue;
    const inputSchema=structuredClone(definition.name===INGEST_TOOL_NAME&&!pathIngest?catalogue.ingestWithoutPath:definition.inputSchema);
    if(project)(inputSchema.properties as Record<string,unknown>).project=structuredClone(PROJECT_ARGUMENT);
    result.push({name:definition.name,description:definition.description,inputSchema,annotations:definition.annotations});
  }
  return result;
}
/** Canonicalize UUID strings, accepting Python's compact/URN/braced spelling. */
export function uuid({value,field}:{value:unknown;field:string}):string {
  if(typeof value!=='string')throw new InputValidationError({detail:`${field} must be a UUID string`});
  const compact=value.replace(/^urn:uuid:/,'').replace(/^\{|\}$/g,'').replace(/-/g,'');
  if(!/^[0-9a-fA-F]{32}$/.test(compact))throw new InputValidationError({detail:`${field} must be a UUID`});
  return compact.toLowerCase().replace(/^(.{8})(.{4})(.{4})(.{4})(.{12})$/,'$1-$2-$3-$4-$5');
}
/** Require the safe identifier shape used by the saved-query registry. */
export function validateSavedQueryIdentifier({value,field}:{value:unknown;field:string}):string {
  if(typeof value!=='string'||!(/^[a-z][a-z0-9_]*$/).test(value))throw queryArgumentError({error:new InputValidationError({detail:`${field} must match ^[a-z][a-z0-9_]*$`})});
  return value;
}
/** A host-owned, opt-in path reader; the base client never loads MCP path settings. */
export type PathBodyResolver=(options:{path:string;filename?:string;mime?:string;maxBodyBytes?:number})=>Promise<{content:Uint8Array;filename:string;mime:string}>;
/** Refuse an invalid local tool argument without reaching a backend. */
function refuse({detail}:{detail:string}):never {throw new ToolArgumentError({error:invalidArguments({detail})});}
/** Read nullable optional strings with source-defined character limits. */
function optionalString({arguments:args,name,nonempty=true,max}:{arguments:Record<string,unknown>;name:string;nonempty?:boolean;max?:number}):string|null {
  const value=args[name];if(value==null)return null;
  if(typeof value!=='string')refuse({detail:`${name} must be a string.`});
  if(nonempty&&!value)refuse({detail:`${name} must be non-empty when set.`});
  if(max!==undefined&&Array.from(value).length>max)refuse({detail:`${name} must be at most ${max} characters.`});
  return value;
}
/** Reject body size using the capability supplied by the host, never an invented cloud cap. */
function bodySize({content,maxBodyBytes}:{content:Uint8Array;maxBodyBytes?:number}):void {
  if(content.length===0)throw new ToolArgumentError({error:new ToolError({code:'empty_body',detail:'Ingest body is empty.',status_code:null,retryable:false,agent_action:'Provide non-empty path / text / content_base64 content.'})});
  if(maxBodyBytes!==undefined&&content.length>maxBodyBytes)throw new ToolArgumentError({error:new ToolError({code:'body_too_large',detail:`Ingest body exceeds the deployment capability limit of ${maxBodyBytes} bytes.`,status_code:null,retryable:false,agent_action:'Split or shorten the document; do not retry the same payload.'})});
}
/** Parse an optional timestamp while requiring UTC rather than silently shifting zones. */
export function utcTimestamp({value,field}:{value:string|Date;field:string}):string {
  if(value instanceof Date) {if(!Number.isFinite(value.getTime()))throw new InputValidationError({detail:`${field} must be a valid UTC timestamp`});return value.toISOString();}
  if(typeof value!=='string'||!/(Z|\+00:00|-00:00)$/.test(value)||!Number.isFinite(Date.parse(value)))throw new InputValidationError({detail:`${field} must be timezone-aware UTC`});
  return value;
}
/** Parse all catalogue tools; path resolution is the sole explicit host-I/O injection. */
export async function validateArguments({name,arguments:args,pathResolver,maxBodyBytes}:{name:string;arguments:Record<string,unknown>;pathResolver?:PathBodyResolver;maxBodyBytes?:number}):Promise<Record<string,unknown>> {
  const definition=catalogue.tools.find(item=>item.name===name);
  if(!definition)throw new InputValidationError({detail:'not a memory tool'});
  const query=OPEN_QUERY_TOOL_NAMES.includes(name);
  if(args===null||typeof args!=='object'||Array.isArray(args)) {if(query)throw queryArgumentError({error:new InputValidationError({detail:'arguments must be a JSON object'})});refuse({detail:'arguments must be a JSON object'});}
  const properties=definition.input_schema.properties as Record<string,unknown>;
  const allowed=new Set(Object.keys(properties));if(name===INGEST_TOOL_NAME&&!pathResolver)allowed.delete('path');
  const unknown=Object.keys(args).filter(key=>!allowed.has(key)).sort();
  if(unknown.length) {if(query)throw queryArgumentError({error:new InputValidationError({detail:'unknown argument keys'})});refuse({detail:`Unknown argument keys: ${unknown.join(', ')}.`});}
  if(maxBodyBytes!==undefined&&(!Number.isSafeInteger(maxBodyBytes)||maxBodyBytes<=0))refuse({detail:'maxBodyBytes must be a positive integer.'});
  if(name===INGEST_TOOL_NAME)return parseIngest({args,pathResolver,maxBodyBytes});
  if(query) {
    try {
    validateSchema({schema:definition.input_schema,value:args});const result={...args};
    if(name==='query_sql'||name==='explain_sql'||name==='run_saved_query')result.parameters??=[];
    if(name==='describe_query_space'){result.pattern??=null;result.include_examples??=false;}
    if(name==='search_query_space')result.k??=10;
    if(name==='list_saved_queries'){result.namespace??=null;result.status??=null;}
    if(name==='describe_saved_query'||name==='run_saved_query')result.version??=null;
    for(const field of ['namespace','name'])if(result[field]!=null&&['list_saved_queries','describe_saved_query','run_saved_query'].includes(name))result[field]=validateSavedQueryIdentifier({value:result[field],field});
    return result;
    }catch(error){if(error instanceof InputValidationError)throw queryArgumentError({error});throw error;}
  }
  try {
    if(name==='pipeline_readiness') {
      validateSchema({schema:definition.input_schema,value:args});
      return {version_ids:(args.version_ids as unknown[]).map(value=>uuid({value,field:'version_id'})),require:validateModel({name:'ReadinessRequirements',value:args.require})};
    }
    if(name==='delete_document')return {doc_id:uuid({value:args.doc_id,field:'doc_id'})};
    if(name==='adjacent_chunks') {
      const window=args.window??1;if(!Number.isInteger(window)||(window as number)<1||(window as number)>2)refuse({detail:'window must be an integer (1 or 2).'});
      return {chunk_id:uuid({value:args.chunk_id,field:'chunk_id'}),window};
    }
    if(name===SECTION_HISTORY_TOOL_NAME||name===DOCUMENT_REFERENCES_TOOL_NAME)return {request:validateModel({name:name===SECTION_HISTORY_TOOL_NAME?'SectionHistoryRequest':'DocumentReferencesRequest',value:args})};
    if(name==='search_documents') {
      const top=new Set(['query','versions','time','k','cursor']);const payload:Record<string,unknown>={filters:{}};
      for(const [key,value]of Object.entries(args)) if(top.has(key))payload[key]=value;else (payload.filters as Record<string,unknown>)[key]=value;
      return {request:validateModel({name:'DocumentSearchRequest',value:payload})};
    }
    const required=definition.input_schema.required as string[]|undefined;
    const missing=(required??[]).filter(key=>!(key in args)).sort();if(missing.length)refuse({detail:`Missing required arguments: ${missing.join(', ')}.`});
    assertJson({value:args});return {...args};
  }catch(error) {if(error instanceof ToolArgumentError)throw error;if(error instanceof InputValidationError)refuse({detail:error.message});throw error;}
}
/** Resolve text/base64 or a caller-owned path and apply source lineage/body rules. */
async function parseIngest({args,pathResolver,maxBodyBytes}:{args:Record<string,unknown>;pathResolver?:PathBodyResolver;maxBodyBytes?:number}):Promise<Record<string,unknown>> {
  const path=optionalString({arguments:args,name:'path'});const text=optionalString({arguments:args,name:'text',nonempty:false});const base64=optionalString({arguments:args,name:'content_base64'});
  if([path,text,base64].filter(value=>value!==null).length!==1)refuse({detail:'Pass exactly one of path, text, or content_base64.'});
  const filename=optionalString({arguments:args,name:'filename',max:512});const mime=optionalString({arguments:args,name:'mime',max:255});
  const title=optionalString({arguments:args,name:'title',nonempty:false,max:512});const source_kind=optionalString({arguments:args,name:'source_kind',max:128});
  const source_ref=optionalString({arguments:args,name:'source_ref',max:512});const source_version_ref=optionalString({arguments:args,name:'source_version_ref',max:512});
  const version_key=optionalString({arguments:args,name:'version_key',max:512});
  const versioning_mode=args.versioning_mode??'snapshot';if(!['snapshot','living'].includes(versioning_mode as string))refuse({detail:"versioning_mode must be 'snapshot' or 'living'."});
  let source_modified_at:string|null=null,effective_from:string|null=null,effective_until:string|null=null;
  try {if(args.source_modified_at!=null)source_modified_at=utcTimestamp({value:args.source_modified_at as string,field:'source_modified_at'});
    if(args.effective_from!=null)effective_from=utcTimestamp({value:args.effective_from as string,field:'effective_from'});
    if(args.effective_until!=null)effective_until=utcTimestamp({value:args.effective_until as string,field:'effective_until'});}
  catch(error) {refuse({detail:(error as Error).message});}
  if((source_kind===null)!==(source_ref===null)||(source_kind===null&&(source_modified_at!==null||source_version_ref!==null||versioning_mode!=='snapshot'||version_key!==null||effective_from!==null||effective_until!==null)))throw new ToolArgumentError({error:new ToolError({code:'source_lineage_pair',detail:'source_kind and source_ref must be supplied together with lineage fields.',status_code:null,retryable:false,agent_action:'Send both source_kind and source_ref, or neither.'})});
  if(effective_until!==null&&effective_from===null)refuse({detail:'effective_until requires effective_from.'});
  if(effective_from!==null) {
    if(versioning_mode!=='snapshot')refuse({detail:'effective periods require snapshot mode.'});
    try{validateModel({name:'EffectivePeriodInput',value:{effective_from,effective_until}});}catch(error){if(error instanceof InputValidationError)refuse({detail:error.message});throw error;}
  }
  let body:{content:Uint8Array;filename:string;mime:string};
  if(path!==null) {
    if(!pathResolver)refuse({detail:'Unknown argument keys: path.'});
    body=await pathResolver({path,filename:filename??undefined,mime:mime??undefined,maxBodyBytes});
    if(!(body.content instanceof Uint8Array)||typeof body.filename!=='string'||!body.filename||typeof body.mime!=='string'||!body.mime)refuse({detail:'path resolver returned an invalid body.'});
  }else {
    if(filename===null)refuse({detail:`filename is required when ${text!==null?'text':'content_base64'} is used.`});
    let content:Uint8Array;
    if(text!==null) {
      if(!text)refuse({detail:'text must be non-empty when used as the body source.'});
      if(!text.isWellFormed())throw new ToolArgumentError({error:new ToolError({code:'encoding_error',detail:'text is not encodable as UTF-8',status_code:null,retryable:false,agent_action:'Remove lone surrogates / invalid code points, or send content_base64 for binary.'})});
      content=new TextEncoder().encode(text);
    }else {
      if(!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(base64!))refuse({detail:'content_base64 is not valid standard base64.'});
      content=Buffer.from(base64!,'base64');
    }
    const inferred=inferUploadMime({filename});body={content,filename,mime:mime??(text!==null&&!inferred.startsWith('text/')?'text/plain':inferred)};
  }
  bodySize({content:body.content,maxBodyBytes});
  return {...body,title:title||null,source_kind,source_ref,source_modified_at,versioning_mode,source_version_ref,version_key,effective_from,effective_until};
}
