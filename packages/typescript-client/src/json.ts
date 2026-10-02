import { InputValidationError, NumericPrecisionError } from './errors';

/** The recursively serializable JSON wire value. */
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

/** Reject unsafe integers using source text before returning any rounded value. */
export function parseJson({ text, statusCode = 200 }: { text: string; statusCode?: number }): unknown {
  /** JSON's reviver supplies the original primitive token on supported Node versions. */
  function revive(_key: string, value: unknown, context?: { source?: string }): unknown {
    if (typeof value === 'number') {
      const source = context?.source;
      if (!source) throw new Error('Node runtime lacks JSON source access');
      const literalInteger = /^-?\d+$/.test(source) ? BigInt(source) : undefined;
      if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))
        || (literalInteger !== undefined && (literalInteger > BigInt(Number.MAX_SAFE_INTEGER)
          || literalInteger < BigInt(Number.MIN_SAFE_INTEGER)))) {
        throw new NumericPrecisionError({ statusCode, detail: 'response contains an unsafe JSON number', code: 'numeric.precision' });
      }
    }
    return value;
  }
  return JSON.parse(text, revive) as unknown;
}

/** Refuse non-JSON values and unsafe input integers without serializing customer data. */
export function assertJson({ value, seen = new WeakSet<object>() }: { value: unknown; seen?: WeakSet<object> }): void {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) {
      throw new InputValidationError({ detail: 'input contains an unsafe JSON number; use an explicit SQL string/cast', code: 'numeric.precision' });
    }
    return;
  }
  if (value !== null && typeof value === 'object' && (Array.isArray(value)
    || Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null)) {
    if (seen.has(value)) throw new InputValidationError({ detail: 'input contains a JSON cycle' });
    seen.add(value);
    for (const item of (Array.isArray(value) ? value : Object.values(value))) assertJson({ value: item, seen });
    seen.delete(value);
    return;
  }
  throw new InputValidationError({ detail: 'input must contain only JSON values' });
}
