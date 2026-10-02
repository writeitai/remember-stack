import type { OutputPipelineReadinessReport } from './generated';

/** Details common to HTTP, validation and connection failures. */
export interface ApiErrorOptions {
  detail?: string;
  statusCode?: number;
  code?: string;
  response?: Response;
  retryable?: boolean;
  requestId?: string;
  cause?: unknown;
}

/** An HTTP error, or status 0 when no HTTP response arrived. */
export class MemoryApiError extends Error {
  readonly statusCode: number;
  readonly detail: string;
  readonly code?: string;
  readonly response?: Response;
  readonly retryable?: boolean;
  readonly requestId?: string;
  /** Keep structured fields without logging headers, URLs or request bodies. */
  constructor(options: ApiErrorOptions = {}) {
    const { detail = '', statusCode = 0, cause } = options;
    super(`API ${statusCode}: ${detail}`, { cause });
    this.name = new.target.name;
    this.statusCode = statusCode;
    this.detail = detail;
    this.code = options.code;
    this.response = options.response;
    this.retryable = options.retryable;
    this.requestId = options.requestId;
  }
}

/** Admission refusal with an advisory server delay in seconds; never auto-retried. */
export class RateLimited extends MemoryApiError {
  readonly retryAfter?: number;
  /** Bind the admission response and its Retry-After hint. */
  constructor(options: Omit<ApiErrorOptions, 'statusCode'> & { retryAfter?: number }) {
    super({ ...options, statusCode: 429 });
    this.retryAfter = options.retryAfter;
  }
}
/** Project resolution failed; a signed key never falls back to localhost. */
export class ProjectResolutionError extends MemoryApiError {}
/** The recorded origin does not permit sending a file-loaded key here. */
export class StoredKeyRefused extends MemoryApiError {}
/** This issuer or self-hosted connection has no account API. */
export class AccountApiUnavailable extends MemoryApiError {}
/** Issuer metadata, discovery or transport violated its contract. */
export class IssuerError extends MemoryApiError {}
/** A response number cannot be represented without integer precision loss. */
export class NumericPrecisionError extends MemoryApiError {}
/** The request deadline expired; no host refresh or replay is allowed. */
export class RequestTimeoutError extends MemoryApiError {
  /** Keep timeout distinct from caller cancellation and readiness deadlines. */
  constructor() { super({ detail: 'request timed out', statusCode: 0 }); }
}
/** A named input failed validation before HTTP. */
export class InputValidationError extends TypeError {
  readonly code: string;
  /** Bind a safe explanation without embedding customer input values. */
  constructor({ detail, code = 'invalid_parameter' }: { detail: string; code?: string }) {
    super(detail);
    this.name = new.target.name;
    this.code = code;
  }
}
/** A local credential file or key is invalid. */
export class CredentialError extends InputValidationError {}
/** A caller cancelled the operation; the caller's own reason is its cause. */
export class AbortError extends Error {
  /** Preserve cancellation identity even for an AbortSignal.timeout reason. */
  constructor({ cause }: { cause?: unknown } = {}) {
    super('operation aborted', { cause });
    this.name = 'AbortError';
  }
}
/** A waited-on pipeline stage exhausted its retries. */
export class PipelineDeadLettered extends Error {
  readonly deadLettered: ReadonlyArray<readonly [string, string, string]>;
  readonly report: OutputPipelineReadinessReport;
  /** Retain all failed stage identities and the last validated readiness report. */
  constructor({ deadLettered, report }: {
    deadLettered: ReadonlyArray<readonly [string, string, string]>;
    report: OutputPipelineReadinessReport;
  }) {
    super('pipeline processing stopped: a stage is dead_letter');
    this.name = 'PipelineDeadLettered';
    this.deadLettered = deadLettered;
    this.report = report;
  }
}
/** Readiness deadline, with the most recent report if a poll completed. */
export class TimeoutError extends Error {
  readonly report?: OutputPipelineReadinessReport;
  /** Distinguish readiness expiry from HTTP request and caller signal timeouts. */
  constructor({ report }: { report?: OutputPipelineReadinessReport } = {}) {
    super('timed out waiting for pipeline readiness');
    this.name = 'TimeoutError';
    this.report = report;
  }
}
/** Connector lookup failed; outside MemoryApiError as in Python. */
export class ConnectorNotFoundError extends Error { override name = 'ConnectorNotFoundError'; }
