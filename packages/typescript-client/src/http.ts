import {Agent as HttpAgent,request as httpRequest} from 'node:http';
import {Agent as HttpsAgent,request as httpsRequest} from 'node:https';
import { AbortError, InputValidationError, MemoryApiError, NumericPrecisionError, RateLimited, RequestTimeoutError } from './errors';
import { parseJson } from './json';
import constants from './constants.generated';

/** Fields of one request; every adapter must send once and honor cancellation. */
export type HttpHeaders = Headers | Record<string,string> | [string,string][];
export interface RequestFields { method:string; query?:URLSearchParams; headers?:HttpHeaders; body?:string|Uint8Array; signal?:AbortSignal; }
/** Caller-owned relative-path adapter with its own base URL and headers. */
export interface HttpClient { request(options:RelativeHttpRequest):Promise<Response>; }
/** Caller-owned absolute-URL transport retaining SDK headers and routing. */
export interface HttpTransport { request(options:AbsoluteHttpRequest):Promise<Response>; }
export interface RelativeHttpRequest extends RequestFields { path:string; url?:never; }
export interface AbsoluteHttpRequest extends RequestFields { url:string; path?:never; }
export type HttpRequest = RelativeHttpRequest|AbsoluteHttpRequest;
export interface ClientAgents { http?:HttpAgent; https?:HttpsAgent; }
export interface OwnedTransport { transport:HttpTransport; close():void; }

/** Create a single-send Node transport; close destroys only agents we create. */
export function nodeHttp({agents={}}:{agents?:ClientAgents}={}):OwnedTransport {
  const httpAgent=agents.http??new HttpAgent({keepAlive:true,timeout:4000});
  const httpsAgent=agents.https??new HttpsAgent({keepAlive:true,timeout:4000});
  return {
    /** Release SDK resources without touching borrowed agent pools. */
    close():void {if(!agents.http)httpAgent.destroy();if(!agents.https)httpsAgent.destroy();},
    transport:{
      /** Send exactly once and buffer the response within the caller's deadline. */
      async request({method,url,query,headers,body,signal}:AbsoluteHttpRequest):Promise<Response> {
        if(signal?.aborted)throw signal.reason;
        const target=new URL(url);
        if(query)for(const[name,value]of query)target.searchParams.append(name,value);
        if(!['http:','https:'].includes(target.protocol))throw new InputValidationError({detail:'transport requires HTTP or HTTPS'});
        const fields:Record<string,string>={};
        new Headers(headers).forEach((value,name)=>{fields[name]=value;});
        if(body!==undefined&&!('content-length'in fields))fields['content-length']=String(typeof body==='string'?Buffer.byteLength(body):body.byteLength);
        return new Promise<Response>((resolve,reject)=>{
          const send=target.protocol==='https:'?httpsRequest:httpRequest;
          const request=send(target,{method,headers:fields,signal,agent:target.protocol==='https:'?httpsAgent:httpAgent},response=>{
            // Stop queued requests from taking a socket which rejected this host.
            if(response.statusCode===421)request.shouldKeepAlive=false;
            const chunks:Buffer[]=[];
            response.on('data',(chunk:Buffer)=>chunks.push(chunk));
            response.once('error',error=>reject(signal?.aborted?signal.reason:transportError({error})));
            response.once('end',()=>{
              const status=response.statusCode??500;
              const responseHeaders=new Headers();
              for(let i=0;i<response.rawHeaders.length;i+=2)responseHeaders.append(response.rawHeaders[i]!,response.rawHeaders[i+1]!);
              try{resolve(new Response([204,205,304].includes(status)?null:Buffer.concat(chunks),{status,headers:responseHeaders}));}
              catch(error){reject(transportError({error}));}
            });
          });
          request.once('error',error=>reject(signal?.aborted?signal.reason:transportError({error})));
          // Idle agent timeouts are not active-operation timeouts.
          request.end(body);
        });
      },
    },
  };
}
const processTransport=nodeHttp().transport;
const pairedTransports=new WeakMap<HttpAgent,WeakMap<HttpsAgent,HttpTransport>>();
const processHttp=new HttpAgent({keepAlive:true,timeout:4000});
const processHttps=new HttpsAgent({keepAlive:true,timeout:4000});
/** Share discovery only across identical borrowed transport or agent objects. */
export function discoveryTransport({transport,agents}:{transport?:HttpTransport;agents?:ClientAgents}={}):HttpTransport {
  if(transport)return transport;
  if(!agents)return processTransport;
  const http=agents.http??processHttp;const https=agents.https??processHttps;
  let pairs=pairedTransports.get(http);
  if(!pairs){pairs=new WeakMap();pairedTransports.set(http,pairs);}
  let result=pairs.get(https);
  if(!result){result=nodeHttp({agents:{http,https}}).transport;pairs.set(https,result);}
  return result;
}

/** Normalize network failures from Node requests and caller-owned adapters consistently. */
export function transportError({ error }: { error: unknown }): MemoryApiError | AbortError {
  if(error instanceof MemoryApiError || error instanceof AbortError)return error;
  const code=(error as {cause?:{code?:string};code?:string})?.cause?.code ?? (error as {code?:string})?.code;
  if(['ETIMEDOUT','ESOCKETTIMEDOUT','UND_ERR_CONNECT_TIMEOUT','UND_ERR_HEADERS_TIMEOUT','UND_ERR_BODY_TIMEOUT'].includes(code??'')
    ||(error instanceof Error && error.name==='TimeoutError'))return new RequestTimeoutError({cause:error});
  return new MemoryApiError({statusCode:0,detail:'network request failed',cause:error});
}

/** Bound routing, network and body reads; a caller abort keeps its own identity. */
export async function bounded<T>({ work, signal, timeoutMs = 30000, controllers }: {
  work: (signal: AbortSignal) => Promise<T>;
  signal?: AbortSignal;
  timeoutMs?: number;
  controllers?: Set<AbortController>;
}): Promise<T> {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new InputValidationError({ detail: 'timeoutMs must be positive and finite' });
  if (signal?.aborted) throw new AbortError({ cause: signal.reason });
  const controller = new AbortController();
  controllers?.add(controller);
  /** Relay user cancellation without confusing its reason with our deadline. */
  const cancel = (): void => controller.abort(new AbortError({ cause: signal?.reason }));
  signal?.addEventListener('abort', cancel, { once: true });
  const timer = setTimeout(() => controller.abort(new RequestTimeoutError()), timeoutMs);
  let rejectAbort: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    /** Reject even when an injected adapter ignores AbortSignal. */
    rejectAbort = (): void => reject(controller.signal.reason);
    controller.signal.addEventListener('abort', rejectAbort, { once: true });
  });
  try { return await Promise.race([work(controller.signal), aborted]); }
  finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', cancel);
    if (rejectAbort) controller.signal.removeEventListener('abort', rejectAbort);
    controllers?.delete(controller);
  }
}

/** A waiter can cancel shared discovery without aborting another client's work. */
export async function waitShared<T>({ promise, signal }: { promise: Promise<T>; signal?: AbortSignal }): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) throw new AbortError({ cause: signal.reason });
  let cancel: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    /** Preserve the cancelling waiter's reason without poisoning the cache. */
    cancel = (): void => reject(new AbortError({ cause: signal.reason }));
    signal.addEventListener('abort', cancel, { once: true });
  });
  try { return await Promise.race([promise, aborted]); }
  finally { if (cancel) signal.removeEventListener('abort', cancel); }
}

/** Decode response text without returning rounded integers or malformed JSON. */
export async function responseJson({ response }: { response: Response }): Promise<unknown> {
  try { return parseJson({ text: await response.text(), statusCode: response.status }); }
  catch (error) {
    if (error instanceof NumericPrecisionError || error instanceof AbortError || error instanceof RequestTimeoutError) throw error;
    throw new MemoryApiError({ statusCode: response.status, detail: 'response did not contain valid JSON' });
  }
}

/** Decode exact public errors at their bound status, without generic retry policy. */
export async function checkedResponse({ response, path }: { response: Response; path: string }): Promise<unknown> {
  if (response.ok) return responseJson({ response });
  const text = await response.text();
  let payload: unknown;
  try { payload = parseJson({ text, statusCode: response.status }); } catch { payload = undefined; }
  const envelope = payload !== null && typeof payload === 'object' && !Array.isArray(payload)
    ? payload as Record<string, unknown> : undefined;
  const detailObject = envelope?.detail !== null && typeof envelope?.detail === 'object' && !Array.isArray(envelope.detail)
    ? envelope.detail as Record<string, unknown> : undefined;
  if (response.status === 429) {
    const hint = response.headers.get('retry-after');
    const retryAfter = hint !== null && hint.trim() !== '' && Number.isFinite(Number(hint)) ? Number(hint) : undefined;
    const code = typeof detailObject?.code === 'string' ? detailObject.code : undefined;
    const detail = typeof detailObject?.message === 'string' ? detailObject.message : typeof envelope?.detail === 'string' ? envelope.detail : 'rate limited';
    throw new RateLimited({ detail, code, retryAfter });
  }
  let detail = text;
  let code: string | undefined;
  let retryable: boolean | undefined;
  let requestId: string | undefined;
  if (envelope && 'detail' in envelope) {
    if (Object.keys(envelope).length !== 1) detail = 'deployment API returned a malformed error envelope';
    else if (detailObject) {
      if (path.startsWith('/query/')) {
        const keys = Object.keys(detailObject);
        const valid = keys.includes('code') && keys.includes('message') && keys.every(key => ['code','message','retryable','request_id'].includes(key))
          && typeof detailObject.code === 'string' && typeof detailObject.message === 'string' && detailObject.message.length > 0
          && (constants.queryErrorStatus as Record<string, number>)[detailObject.code] === response.status
          && (!('retryable' in detailObject) || typeof detailObject.retryable === 'boolean')
          && (!('request_id' in detailObject) || typeof detailObject.request_id === 'string');
        if (valid) {
          code = detailObject.code as string; detail = detailObject.message as string;
          retryable = detailObject.retryable as boolean | undefined; requestId = detailObject.request_id as string | undefined;
        } else detail = 'deployment API returned a malformed structured error';
      } else detail = JSON.stringify(detailObject);
    } else detail = typeof envelope.detail==='object' ? JSON.stringify(envelope.detail) : String(envelope.detail);
  }
  throw new MemoryApiError({ statusCode: response.status, detail, code, retryable, requestId, response });
}

/** Classify reads from the engine-owned route scope including read-only POSTs. */
export function readRoute({ method, path }: { method: string; path: string }): boolean {
  return constants.readRoutes.some(route => route.method === method.toUpperCase() && new RegExp(route.pattern).test(path));
}

/** Only 421 or a 404 without an engine envelope indicates apparent movement. */
export async function looksMoved({ response }: { response: Response }): Promise<boolean> {
  if (response.status === 421) return true;
  if (response.status !== 404) return false;
  try {
    const payload = await response.clone().json() as unknown;
    return !(payload !== null && typeof payload === 'object' && !Array.isArray(payload) && 'detail' in payload);
  } catch { return true; }
}
