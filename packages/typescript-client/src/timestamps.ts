import { InputValidationError } from './errors';

const isoTimestamp = /^(\d{4}-\d{2}-\d{2})[Tt ](\d{2}:\d{2})(?::(\d{2})(?:[.,](\d+))?)?([Zz]|[+-]\d{2}(?::?\d{2})?)?$/;

export const OFFSET_REQUIRED = 'must be timezone-aware (include Z or a numeric UTC offset)';

/** Convert an aware instant to UTC at the same microsecond precision as Python. */
export function utcTimestamp({ value, field }: { value: unknown; field: string }): string {
  if (value instanceof Date && Number.isFinite(value.getTime())) {
    value = value.toISOString();
  }
  const match = typeof value === 'string' ? isoTimestamp.exec(value) : null;
  if (!match || match[0] !== value || match[1]?.startsWith('0000')) {
    throw new InputValidationError({ detail: field + ' must be a valid ISO 8601 timestamp' });
  }
  const [, date, time, second = '00', fractionalSeconds = '', offset] = match;
  if (!offset) {
    throw new InputValidationError({ detail: field + ' ' + OFFSET_REQUIRED });
  }
  const local = date + 'T' + time + ':' + second;
  const wallTime = new Date(local + 'Z');
  const zone = offset.length === 3 ? offset + ':00' : offset.toUpperCase();
  const instant = new Date(local + zone);
  if (!Number.isFinite(wallTime.getTime()) || !Number.isFinite(instant.getTime())
    || wallTime.toISOString().slice(0, 19) !== local) {
    throw new InputValidationError({ detail: field + ' must be a valid ISO 8601 timestamp' });
  }
  const utc = instant.toISOString();
  if (utc.length !== 24 || utc.startsWith('0000')) {
    throw new InputValidationError({ detail: field + ' is outside the supported UTC date range' });
  }
  // The offset shifts whole seconds. Retain the source fraction beyond Date's milliseconds.
  const fraction = fractionalSeconds.slice(0, 6).padEnd(6, '0');
  return utc.slice(0, 19) + (fraction === '000000' ? '' : '.' + fraction) + '+00:00';
}
