import { isIP } from 'node:net';
import type { OutputKeyClaims, OutputIssuerMetadata } from './generated';
import { InputValidationError, IssuerError, AbortError, RequestTimeoutError } from './errors';
import { parseJson } from './json';
import { validateModel } from './validation';
import { bounded, responseJson, waitShared, type HttpTransport, type HttpRequest } from './http';
/** Unverified signed-key claims, used only for routing and display. */
export class KeyClaims implements OutputKeyClaims {
  readonly iss: string; readonly jti: string | null; readonly sub: string | null;
  readonly org: string | null; readonly exp: number | null;
  readonly projects: string[] | 'org:*' | null; readonly permissions: string[];
  /** Validate source-defined claims and apply their defaults. */
  constructor({ value }: { value: unknown }) {
    const d = validateModel<OutputKeyClaims>({ name: 'KeyClaims', value });
    this.iss=d.iss; this.jti=d.jti; this.sub=d.sub; this.org=d.org;
    this.exp=d.exp; this.projects=d.projects; this.permissions=d.permissions;
  }
  /** Check coverage without asserting that a signature was verified. */
  covers({ projectId }: { projectId: string }): boolean { return this.projects === 'org:*' || (this.projects?.includes(projectId) ?? false); }
  /** Return the source expiry as an ISO UTC timestamp. */
  get expiresAt(): string | null { return this.exp === null ? null : new Date(this.exp * 1000).toISOString(); }
}
/** Distinguish shared keys from JWS; malformed signed-looking keys fail closed. */
export function signedKeyClaims({ key }: { key: string }): KeyClaims | null {
  let token=key.trim();
  if (!token.startsWith('eyJ')) { const match=/^[A-Za-z]+_(eyJ.*)$/.exec(token); if (!match) return null; token=match[1]!; }
  const parts=token.split('.');
  if (parts.length !== 3) throw new InputValidationError({ detail: 'the key looks like a signed key but is not a JWS' });
  try {
    if (!/^[A-Za-z0-9_-]+={0,2}$/.test(parts[1]!)) throw new Error('invalid base64url');
    return new KeyClaims({ value: parseJson({ text: Buffer.from(parts[1]!, 'base64url').toString('utf8') }) });
  } catch { throw new InputValidationError({ detail: 'the key looks like a signed key but its claims cannot be read' }); }
}
/** Recognize literal localhost and IPv4/IPv6 loopback. */
export function isLoopback({ host }: { host: string }): boolean {
  const bare=host.replace(/^\[|\]$/g,'').toLowerCase();
  return bare==='localhost' || (isIP(bare)===4 && bare.startsWith('127.')) || (isIP(bare)===6 && bare==='::1');
}
/** Accept HTTPS or loopback HTTP; URL userinfo is forbidden. */
export function requireSecureUrl({ url, what }: { url: string; what: string }): URL {
  let parsed: URL;
  try { parsed=new URL(url); } catch { throw new IssuerError({ detail: `${what} is not a valid URL` }); }
  if (!parsed.username && !parsed.password && (parsed.protocol==='https:' || (parsed.protocol==='http:' && isLoopback({ host: parsed.hostname })))) return parsed;
  throw new IssuerError({ detail: `${what} must be https (or http on a loopback address) without URL credentials` });
}
/** Canonical scheme, hostname and effective port. */
export type NormalizedOrigin = readonly [string, string, number | null];
/** Normalize origins independently of paths and trailing DNS dots. */
export function origin({ url }: { url: string | URL }): NormalizedOrigin {
  const p=new URL(url);
  return [p.protocol.slice(0,-1),p.hostname.replace(/\.$/,'').toLowerCase(),p.port?Number(p.port):p.protocol==='https:'?443:p.protocol==='http:'?80:null];
}
/** Compare canonical origins; invalid URLs never compare equal. */
export function sameOrigin({ left, right }: { left: string | URL; right: string | URL }): boolean {
  try { return JSON.stringify(origin({ url:left }))===JSON.stringify(origin({ url:right })); } catch { return false; }
}
/** Trim issuer whitespace/slashes and enforce its security contract. */
export function normalizeIssuer({ issuer }: { issuer: string }): string {
  const text=issuer.trim().replace(/\/+$/,''); requireSecureUrl({ url:text,what:'issuer' }); return text;
}
/** Insert the RFC8414 well-known segment before the issuer path. */
export function metadataUrl({ issuer }: { issuer: string }): string {
  const url=new URL(normalizeIssuer({ issuer }));
  url.pathname='/.well-known/oauth-authorization-server'+url.pathname.replace(/\/+$/,''); url.search=''; return url.toString();
}
/** Preserve method/body/headers across at most three same-origin redirect hops. */
export async function sendSameOrigin({ http, request, maxRedirects=3 }: { http: HttpTransport; request: HttpRequest & {url:string}; maxRedirects?:number }): Promise<Response> {
  let current=request;
  for (let hop=0;hop<=maxRedirects;hop++) {
    const response=await http.request(current);
    if (response.status<300 || response.status>=400) return response;
    const location=response.headers.get('location');
    await response.body?.cancel();
    if (!location) throw new IssuerError({detail:'issuer redirected without a Location'});
    const target=new URL(location,current.url);
    if (!sameOrigin({left:request.url,right:target}) || target.username || target.password) throw new IssuerError({detail:'refusing a cross-origin issuer redirect'});
    current={...current,url:target.toString(),query:undefined};
  }
  throw new IssuerError({detail:'too many issuer redirects'});
}
export type IssuerEndpoint = Exclude<keyof OutputIssuerMetadata,'issuer'|'jwks_uri'>;
/** Validated metadata with checked advertised endpoint lookup. */
export class IssuerMetadata implements OutputIssuerMetadata {
  readonly issuer:string; readonly device_authorization_endpoint:string|null;
  readonly token_endpoint:string|null; readonly revocation_endpoint:string|null; readonly jwks_uri:string|null;
  readonly remember_mcp_endpoint:string|null; readonly remember_project_endpoint:string|null; readonly remember_account_endpoint:string|null;
  /** Validate the source-derived document and all defaulted endpoints. */
  constructor({value}:{value:unknown}) {
    const d=validateModel<OutputIssuerMetadata>({name:'IssuerMetadata',value});
    this.issuer=d.issuer; this.device_authorization_endpoint=d.device_authorization_endpoint;
    this.token_endpoint=d.token_endpoint; this.revocation_endpoint=d.revocation_endpoint; this.jwks_uri=d.jwks_uri;
    this.remember_mcp_endpoint=d.remember_mcp_endpoint; this.remember_project_endpoint=d.remember_project_endpoint;
    this.remember_account_endpoint=d.remember_account_endpoint;
  }
  /** Require a secure endpoint, failing explicitly if the provider lacks it. */
  endpoint({name}:{name:IssuerEndpoint}):string {
    const url=this[name]; if (!url) throw new IssuerError({detail:`issuer does not advertise ${name}`});
    requireSecureUrl({url,what:name}); return url;
  }
}
const metadataCache=new Map<string,IssuerMetadata>();
let pendingMetadata=new WeakMap<HttpTransport,Map<string,Promise<IssuerMetadata>>>();
/** Forget cached metadata for provider changes and test isolation. */
export function clearMetadataCache():void { metadataCache.clear();pendingMetadata=new WeakMap(); }
/** Fetch issuer-bound metadata once per process; each waiter may cancel independently. */
export async function fetchIssuerMetadata({issuer,http,signal,timeoutMs=30000}:{issuer:string;http:HttpTransport;signal?:AbortSignal;timeoutMs?:number}):Promise<IssuerMetadata> {
  const normalized=normalizeIssuer({issuer});
  const cached=metadataCache.get(normalized);
  if(cached)return waitShared({promise:Promise.resolve(cached),signal});
  let pending=pendingMetadata.get(http);
  if(!pending){pending=new Map();pendingMetadata.set(http,pending);}
  let promise=pending.get(normalized);
  if (!promise) {
    promise=bounded({timeoutMs,work:async sharedSignal=>{
      try {
        const response=await sendSameOrigin({http,request:{method:'GET',url:metadataUrl({issuer:normalized}),headers:{Accept:'application/json'},signal:sharedSignal}});
        if (response.status!==200) throw new IssuerError({detail:'issuer metadata is unavailable'});
        const metadata=new IssuerMetadata({value:await responseJson({response})});
        if (metadata.issuer.replace(/\/+$/,'')!==normalized) throw new IssuerError({detail:'issuer metadata names a different issuer'});
        metadataCache.set(normalized,metadata);return metadata;
      } catch (error) {
        if (error instanceof IssuerError || error instanceof AbortError || error instanceof RequestTimeoutError) throw error;
        throw new IssuerError({detail:'issuer metadata is unavailable or malformed'});
      }
    }});
    pending.set(normalized,promise);const shared=promise;const requests=pending;
    promise.finally(()=>{if(requests.get(normalized)===shared)requests.delete(normalized);}).catch(()=>{});
  }
  return waitShared({promise,signal});
}
