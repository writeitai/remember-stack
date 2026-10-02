import { AbortError, InputValidationError, MemoryApiError, NumericPrecisionError, RateLimited, RequestTimeoutError } from './errors';
import { parseJson } from './json';
import constants from './constants.generated';

/** A caller-owned adapter, with its own base URL, headers, timeout and lifecycle. */
export interface HttpClient {
  request(options: HttpRequest): Promise<Response>;
}
/** SDK requests use url; caller-owned client injection receives path instead. */
export interface HttpRequest {
  method: string;
  url?: string;
  path?: string;
  query?: URLSearchParams;
  headers?: HeadersInit;
  body?: BodyInit;
  signal?: AbortSignal;
}
/** A replacement fetch retains all SDK routing and security behavior. */
export type Fetch = typeof globalThis.fetch;

/** Create a per-client adapter; redirects remain under SDK control. */
export function fetchHttp({ fetch: implementation = globalThis.fetch }: { fetch?: Fetch } = {}): HttpClient {
  return {
    /** Send to the resolved absolute URL with manual redirects. */
    async request({ method, url, query, headers, body, signal }: HttpRequest): Promise<Response> {
      if (!url) throw new Error('SDK fetch adapter requires an absolute URL');
      const target = new URL(url);
      if (query) for (const [name, value] of query) target.searchParams.append(name, value);
      try { return await implementation(target, { method, headers, body, signal, redirect: 'manual' }); }
      catch (error) {
        if (signal?.aborted) throw signal.reason;
        throw transportError({ error });
      }
    },
  };
}

/** Normalize network failures from native fetch and caller-owned adapters consistently. */
export function transportError({ error }: { error: unknown }): MemoryApiError | AbortError {
  if(error instanceof MemoryApiError || error instanceof AbortError)return error;
  const code=(error as {cause?:{code?:string};code?:string})?.cause?.code ?? (error as {code?:string})?.code;
  if(['ETIMEDOUT','ESOCKETTIMEDOUT','UND_ERR_CONNECT_TIMEOUT','UND_ERR_HEADERS_TIMEOUT','UND_ERR_BODY_TIMEOUT'].includes(code??'')
    ||(error instanceof Error && error.name==='TimeoutError'))return new RequestTimeoutError();
  return new MemoryApiError({statusCode:0,detail:'network request failed'});
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
    const detail = typeof detailObject?.message === 'string' ? detailObject.message : typeof envelope?.detail === 'string' ? envelope.detail : 'request rate limited';
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
    } else detail = String(envelope.detail);
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
