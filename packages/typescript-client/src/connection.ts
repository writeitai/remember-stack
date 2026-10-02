import { createHash } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { inspect } from 'node:util';
import type { OutputResolvedProject } from './generated';
import { SecretString, loadCredentials, environment, type StoredCredentials, type ConnectionEnvironment } from './credentials';
import { KeyClaims, signedKeyClaims, normalizeIssuer, sameOrigin, requireSecureUrl, fetchIssuerMetadata, sendSameOrigin } from './issuer';
import { InputValidationError, ProjectResolutionError, StoredKeyRefused, AbortError, RequestTimeoutError } from './errors';
import { bounded, responseJson, waitShared, type HttpClient } from './http';
import { validateModel } from './validation';
export const DEFAULT_API_URL = 'http://127.0.0.1:8000';
export const HOST_CACHE_TTL_SECONDS = 600;
export type Source = 'explicit' | 'environment' | 'file';
/** Resolved connection settings with deliberately wrapped secret material. */
export class Connection {
  readonly key: SecretString | null; readonly keySource: Source | null;
  readonly apiUrl: string | null; readonly apiUrlSource: Source | null;
  readonly project: string | null; readonly issuer: string | null; readonly mcpUrl: string | null;
  readonly stored: StoredCredentials | null; readonly claims: KeyClaims | null;
  /** Retain source provenance without serializing the bearer. */
  constructor(options: { key: SecretString | null; keySource: Source | null; apiUrl: string | null; apiUrlSource: Source | null; project: string | null; issuer: string | null; mcpUrl: string | null; stored: StoredCredentials | null; claims: KeyClaims | null }) {
    this.key=options.key; this.keySource=options.keySource; this.apiUrl=options.apiUrl; this.apiUrlSource=options.apiUrlSource;
    this.project=options.project; this.issuer=options.issuer; this.mcpUrl=options.mcpUrl; this.stored=options.stored; this.claims=options.claims;
  }
  /** Access the HTTP header deliberately; it is never an enumerable field. */
  get authorization(): string | null { return this.key === null ? null : `Bearer ${this.key.getSecretValue()}`; }
  /** Redact credentials and URL contents from accidental diagnostic serialization. */
  toJSON(): object { return { key: this.key ? '**********' : null, keySource:this.keySource, apiUrl:this.apiUrl ? '[configured]' : null, project:this.project, issuer:this.issuer ? '[configured]' : null }; }
  /** Redact Node's inspection, including nested credential objects. */
  [inspect.custom]():object { return this.toJSON(); }
}
/** Normalize bare/Bearer keys and reject multiline header values. */
export function normalizeKey({value}:{value:string}):string {
  let text=value.trim(); if (/^bearer /i.test(text)) text=text.slice(7).trim();
  if (!text || text.toLowerCase()==='bearer') throw new InputValidationError({detail:'the API key is empty'});
  if (/[\r\n]/.test(text)) throw new InputValidationError({detail:'the API key must not contain line breaks'});
  return text;
}
/** Return only the documented optional issuer environment setting. */
export function environmentIssuer():string|null { return environment().issuer ?? null; }
export interface ConnectionOptions { apiKey?:string|null; apiUrl?:string|null; project?:string|null; issuer?:string|null; mcpUrl?:string|null; env?:ConnectionEnvironment; }
/** Resolve explicit, environment and shared-file settings without making network calls. */
export function resolveConnection({apiKey,apiUrl,project,issuer,mcpUrl,env=environment()}:ConnectionOptions={}):Connection {
  const stored=((apiKey==null && !env.apiKey)||(apiUrl==null && !env.apiUrl)) ? loadCredentials({env}) : null;
  let key:SecretString|null=null; let keySource:Source|null=null;
  const keys:ReadonlyArray<readonly [string|null|undefined,Source]>=[[apiKey,'explicit'],[env.apiKey,'environment'],[stored?.key?.getSecretValue(),'file']];
  for (const [candidate,source] of keys) if(candidate) {key=new SecretString({value:normalizeKey({value:candidate})});keySource=source;break;}
  let resolvedUrl:string|null=null; let apiUrlSource:Source|null=null;
  const urls:ReadonlyArray<readonly [string|null|undefined,Source]>=[[apiUrl,'explicit'],[env.apiUrl,'environment'],[stored?.api_url,'file']];
  for (const [candidate,source] of urls) if(candidate) {resolvedUrl=candidate.trim();apiUrlSource=source;break;}
  const claims=key?signedKeyClaims({key:key.getSecretValue()}):null;
  return new Connection({key,keySource,apiUrl:resolvedUrl,apiUrlSource,project:project||env.project||stored?.default_project||null,
    issuer:issuer||env.issuer||stored?.issuer||claims?.iss||null,mcpUrl:(mcpUrl||env.mcpUrl||'').trim()||null,stored,claims});
}
export type ResolvedProject = OutputResolvedProject;
export type Clock = () => number;
/** Monotonic seconds, matching the source cache clock contract. */
export function monotonic():number {return performance.now()/1000;}
interface CachedProject { created:number; value:ResolvedProject; }
const hostCache=new Map<string,CachedProject>();
const pendingHosts=new Map<string,Promise<ResolvedProject>>();
/** Forget resolved deployment mappings without cancelling active waiters. */
export function clearHostCache():void {hostCache.clear();pendingHosts.clear();}
/** Ask the issuer for a covered project; shared discovery never uses the key id alone. */
export async function resolveProject({key,claims,project,http,clock=monotonic,refresh=false,signal,timeoutMs=30000}:{key:string;claims:KeyClaims;project:string|null;http:HttpClient;clock?:Clock;refresh?:boolean;signal?:AbortSignal;timeoutMs?:number}):Promise<ResolvedProject> {
  const cacheKey=JSON.stringify([normalizeIssuer({issuer:claims.iss}),createHash('sha256').update(key).digest('hex'),project]);
  const cached=hostCache.get(cacheKey);
  if(!refresh && cached && clock()-cached.created < HOST_CACHE_TTL_SECONDS) return waitShared({promise:Promise.resolve(cached.value),signal});
  let pending=pendingHosts.get(cacheKey);
  if(!pending) {
    pending=bounded({timeoutMs,work:async sharedSignal=>{
      try {
        const metadata=await fetchIssuerMetadata({issuer:claims.iss,http,signal:sharedSignal,timeoutMs});
        const endpoint=metadata.endpoint({name:'remember_project_endpoint'});
        if(!sameOrigin({left:claims.iss,right:endpoint})) throw new ProjectResolutionError({detail:'project endpoint must share the issuer origin'});
        const response=await sendSameOrigin({http,request:{method:'GET',url:endpoint,query:project?new URLSearchParams({project}):undefined,headers:{Authorization:`Bearer ${key}`,Accept:'application/json'},signal:sharedSignal}});
        if(response.status!==200) throw new ProjectResolutionError({statusCode:response.status,detail:'issuer could not resolve the project'});
        const resolved=validateModel<ResolvedProject>({name:'ResolvedProject',value:await responseJson({response})});
        if(!claims.covers({projectId:resolved.project})) throw new ProjectResolutionError({detail:'issuer resolved a project which this key does not cover'});
        requireSecureUrl({url:resolved.api_url,what:'resolved deployment URL'});
        hostCache.set(cacheKey,{created:clock(),value:resolved}); return resolved;
      } catch(error) {
        if(error instanceof ProjectResolutionError || error instanceof AbortError || error instanceof RequestTimeoutError) throw error;
        throw new ProjectResolutionError({detail:'issuer returned an unavailable or unusable project resolution'});
      }
    }});
    pendingHosts.set(cacheKey,pending); const shared=pending;
    pending.finally(()=>{if(pendingHosts.get(cacheKey)===shared) pendingHosts.delete(cacheKey);}).catch(()=>{});
  }
  return waitShared({promise:pending,signal});
}
/** Lazy engine routing, with first-project pinning and TTL refresh of its URL. */
export class EngineRoute {
  readonly #connection:Connection; readonly #http:HttpClient; readonly #clock:Clock; readonly #timeoutMs:number;
  #pinned:ResolvedProject|undefined; #resolvedAt=0; #pending:Promise<ResolvedProject>|undefined;
  /** Bind connection and issuer transport without discovery. */
  constructor({connection,http,clock=monotonic,timeoutMs=30000}:{connection:Connection;http:HttpClient;clock?:Clock;timeoutMs?:number}) {this.#connection=connection;this.#http=http;this.#clock=clock;this.#timeoutMs=timeoutMs;}
  /** Whether issuer resolution controls the engine destination. */
  get keyRouted():boolean {return this.#connection.apiUrl===null && this.#connection.claims!==null;}
  /** Get the current URL after checking file-loaded credential destination rules. */
  async target({signal}:{signal?:AbortSignal}={}):Promise<{url:string;authorization:string|null}> {
    const c=this.#connection;
    if(c.apiUrl!==null) {await this.#checkStoredDestination({url:c.apiUrl,signal});return {url:c.apiUrl,authorization:c.authorization};}
    if(c.claims && c.key) return {url:(await this.#resolve({signal})).api_url,authorization:c.authorization};
    if(c.keySource==='file') throw new StoredKeyRefused({detail:'stored key has no engine URL and is not a signed key'});
    return {url:DEFAULT_API_URL,authorization:c.authorization};
  }
  /** Refresh after apparent movement, keeping the original request error on resolution failure. */
  async reResolve({signal}:{signal?:AbortSignal}={}):Promise<boolean> {
    if(!this.keyRouted) return false;
    const previous=(await this.#resolve({signal})).api_url;
    try {const next=await this.#resolve({refresh:true,signal});return previous.replace(/\/+$/,'')!==next.api_url.replace(/\/+$/,'');}
    catch(error) {if(error instanceof AbortError || error instanceof RequestTimeoutError) throw error;return false;}
  }
  /** Resolve once concurrently, then refresh only the pinned project after its TTL. */
  async #resolve({refresh=false,signal}:{refresh?:boolean;signal?:AbortSignal}):Promise<ResolvedProject> {
    if(!refresh && this.#pinned && this.#clock()-this.#resolvedAt<HOST_CACHE_TTL_SECONDS) return this.#pinned;
    if(!this.#pending) {
      const c=this.#connection;
      if(!c.claims || !c.key) throw new ProjectResolutionError({detail:'connection has no signed key'});
      this.#pending=resolveProject({key:c.key.getSecretValue(),claims:c.claims,project:this.#pinned?.project??c.project,http:this.#http,clock:this.#clock,refresh:refresh||this.#pinned!==undefined,timeoutMs:this.#timeoutMs}).then(value=>{
        if(this.#pinned && value.project!==this.#pinned.project) throw new ProjectResolutionError({detail:'issuer changed the pinned project'});
        this.#pinned=value;this.#resolvedAt=this.#clock();return value;
      });
      const pending=this.#pending;
      pending.finally(()=>{if(this.#pending===pending)this.#pending=undefined;}).catch(()=>{});
    }
    return waitShared({promise:this.#pending,signal});
  }
  /** Permit a stored bearer only at its recorded, issuer or resolved deployment origin. */
  async #checkStoredDestination({url,signal}:{url:string;signal?:AbortSignal}):Promise<void> {
    const c=this.#connection;
    if(c.keySource!=='file' || c.apiUrlSource==='file') return;
    if(c.stored?.api_url && sameOrigin({left:url,right:c.stored.api_url})) return;
    if(c.claims && c.key) {
      if(sameOrigin({left:url,right:c.claims.iss})) return;
      try {if(sameOrigin({left:url,right:(await this.#resolve({signal})).api_url})) return;}
      catch(error) {if(error instanceof AbortError || error instanceof RequestTimeoutError) throw error;}
    }
    throw new StoredKeyRefused({detail:'stored key is not permitted at this destination; pass the key explicitly to use it there'});
  }
}
