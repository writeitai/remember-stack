import {isQueryArgumentError} from './query-arguments';
import { AbortError, InputValidationError, MemoryApiError, NumericPrecisionError, RateLimited } from './errors';
/** Source-compatible structured tool error fields use their wire names. */
export interface ToolErrorFields {code:string;detail:string;status_code:number|null;retryable:boolean;agent_action:string;reason_code?:string;request_id?:string;retry_after?:number;}
/** One transport-neutral error envelope for every tool family. */
export class ToolError implements ToolErrorFields {
  readonly code:string;readonly detail:string;readonly status_code:number|null;readonly retryable:boolean;readonly agent_action:string;
  readonly reason_code?:string;readonly request_id?:string;readonly retry_after?:number;
  /** Bind diagnostics without automatically acting on advisory retry hints. */
  constructor(fields:ToolErrorFields) {this.code=fields.code;this.detail=fields.detail;this.status_code=fields.status_code;this.retryable=fields.retryable;this.agent_action=fields.agent_action;this.reason_code=fields.reason_code;this.request_id=fields.request_id;this.retry_after=fields.retry_after;}
  /** Return the exact wire envelope, omitting unset optional fields. */
  asDict():{error:ToolErrorFields} {
    const error:ToolErrorFields={code:this.code,detail:this.detail,status_code:this.status_code,retryable:this.retryable,agent_action:this.agent_action};
    if(this.reason_code!==undefined) error.reason_code=this.reason_code;
    if(this.request_id!==undefined) error.request_id=this.request_id;
    if(this.retry_after!==undefined) error.retry_after=this.retry_after;
    return {error};
  }
}
/** Validation or body resolution failed before a backend call. */
export class ToolArgumentError extends Error {
  readonly error:ToolError;
  /** Preserve the structured validation refusal. */
  constructor({error}:{error:ToolError}) {super(error.detail);this.name='ToolArgumentError';this.error=error;}
}
/** Produce a safe local invalid-argument refusal. */
export function invalidArguments({detail}:{detail:string}):ToolError {return new ToolError({code:'invalid_arguments',detail,status_code:null,retryable:false,agent_action:'Fix the tool arguments and retry.'});}
/** Render a transport-neutral MCP result; hosts own tool execution. */
export function errorResult({error}:{error:ToolError}):{content:{type:'text';text:string}[];isError:true} {return {content:[{type:'text',text:JSON.stringify(error.asDict())}],isError:true};}
const retryableQuery=new Set(['quota_exceeded','concurrency_exceeded','saved_query_revalidation_pending','statement_timeout','lock_timeout','pg_unavailable','p1_unavailable','graph_unavailable','corpus_body_unavailable','generation_unavailable']);
const spend=new Set(['spend_safety','reservation_refused','spend_cap','budget_exceeded','spend_reservation_refused']);
const prefixes=new Set([...spend,'dispatch_refused','dispatch_parked','body_too_large','empty_body']);
/** Map SDK failures to source-compatible errors without exposing unexpected exception text. */
export function mapError({error}:{error:unknown}):ToolError {
  if(error instanceof NumericPrecisionError) return new ToolError({code:'local_backend_error',detail:error.detail,status_code:null,retryable:false,agent_action:'Report a composition/contract defect; do not retry the same call.'});
  if(error instanceof AbortError) return new ToolError({code:'cancelled',detail:'operation aborted',status_code:null,retryable:false,agent_action:'The caller cancelled the operation.'});
  if(error instanceof InputValidationError) {
    if(isQueryArgumentError({error}))return new ToolError({code:error.code,detail:error.message,status_code:null,retryable:false,agent_action:'Read the detail and fix the query or its arguments.'});
    return invalidArguments({detail:error.message});
  }
  if(error instanceof MemoryApiError) {
    const status=error.statusCode;const detail=error.detail;const separator=detail.indexOf(':');
    const head=separator<0?detail:detail.slice(0,separator);
    let code=separator>=0&&prefixes.has(head)?head:detail;
    const reason_code=separator>=0&&prefixes.has(head)?detail.slice(separator+1)||undefined:undefined;
    if(error.code)code=error.code;
    const base={detail,status_code:status,reason_code,request_id:error.requestId};
    if(status===0) return new ToolError({...base,code:'transport_error',retryable:true,agent_action:'Retry with back-off; check the deployment URL and network.'});
    if(spend.has(code)||detail.startsWith('spend_safety')) return new ToolError({...base,code:'spend_safety',detail:detail||'Spend or reservation safety refused this work.',retryable:false,agent_action:'Surface the spend/reservation refusal to the user/operator; do not busy-retry. Adjust budgets or wait for a new reservation.'});
    if(status===429) return new ToolError({...base,code:['rate_limited','concurrency_limited'].includes(code)?code:'rate_limited',detail:detail||'Too many requests.',retryable:true,retry_after:error instanceof RateLimited?error.retryAfter:undefined,agent_action:'Wait retry_after seconds (if given), then retry; do not retry sooner. Lower the request rate or run fewer calls at once.'});
    if(code==='body_too_large'||status===413) return new ToolError({...base,code:'body_too_large',detail:detail||'Ingest body exceeds the deployment size limit.',retryable:false,agent_action:'Split or shorten the document; do not retry the same payload.'});
    if(code==='empty_body') return new ToolError({...base,code,retryable:false,agent_action:'Provide non-empty path / text / content_base64 content.'});
    if(code==='dispatch_refused') return new ToolError({...base,code,retryable:false,agent_action:'Surface the reason to the user/operator; do not busy-retry. Typical causes: spend cap, missing policy, halt.'});
    if(code==='dispatch_parked') return new ToolError({...base,code,retryable:false,agent_action:'Stop automated retries and notify a human; park is policy, not a transient blip.'});
    if(status===401) return new ToolError({...base,code:'unauthorized',detail:detail||'Unauthorized.',retryable:false,agent_action:'The key is missing, expired or revoked: run `remember login` or replace REMEMBER_API_KEY.'});
    if(status===403) return new ToolError({...base,code:'insufficient_permission',detail:detail||'Forbidden.',retryable:false,agent_action:'The key may not do this here: use a key with the needed permission for this deployment.'});
    const retryable=status>=500||retryableQuery.has(code);
    return new ToolError({...base,code:error.code?code:status>=500?'engine_unavailable':'engine_client_error',detail:detail||`Engine answered HTTP ${status}.`,retryable,agent_action:retryable?'Retry with back-off (3–5 attempts, 2s→30s). If still failing, report an operator outage.':'Read the detail; fix the call. Do not retry it unchanged.'});
  }
  return new ToolError({code:'internal_error',detail:'Unexpected internal failure; the server logged the details.',status_code:null,retryable:false,agent_action:'Unexpected internal failure. Do not busy-retry; report the error (and any request_id) to an operator or as a product defect.'});
}
