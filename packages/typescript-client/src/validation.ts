import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import type { ValidateFunction } from 'ajv';
import schemas from './schemas.generated';
import constants from './constants.generated';
import { InputValidationError, MemoryApiError } from './errors';
import { assertJson } from './json';

/** JSON Schema vocabulary used by same-revision Pydantic contracts. */
interface Schema {
  $ref?: string;
  type?: string;
  properties?: Record<string, Schema>;
  additionalProperties?: boolean | Schema;
  items?: Schema;
  anyOf?: Schema[];
  oneOf?: Schema[];
  default?: unknown;
  'x-extra'?: string;
  'x-exclude'?: boolean;
  format?:string;
}
const definitions = schemas.$defs as unknown as Record<string, Schema>;
const ajv = new Ajv2020({ strict: false, allErrors: true, validateFormats: true });
addFormats(ajv);
ajv.addKeyword({ keyword: 'x-extra', schemaType: 'string' });
ajv.addKeyword({ keyword: 'x-utc', schemaType: 'boolean', validate: utcKeyword });
ajv.addSchema({ ...schemas, $id: 'urn:remember-client:schemas' });
const cache = new Map<string, ValidateFunction>();
const variantCache = new WeakMap<Schema, ValidateFunction>();
const semanticValidators = {
  DocumentSearchRequest: ['cursor_pages_filters_only'], ConnectorCreate: ['_credentials_are_references'],
  OverlapTemporalScope: ['_ordered'], EvidenceSpan: ['_end_after_start'],
  EvidenceTotal: ['_returned_does_not_exceed_total'], ContextBundleV2: ['_child_grains_are_exact'],
};
for (const [name, validators] of Object.entries(constants.modelValidators)) {
  if (JSON.stringify(semanticValidators[name as keyof typeof semanticValidators]) !== JSON.stringify(validators)) {
    throw new Error(`unimplemented Python model validators: ${name}`);
  }
}

/** AJV callback for the source-owned UTC-only field annotation. */
function utcKeyword(enabled: boolean, value: unknown): boolean {
  return !enabled || value === null || (typeof value === 'string'
    && /(?:[zZ]|[+]00:00)$/.test(value) && Number.isFinite(Date.parse(value)));
}

/** Resolve a local model reference without accepting remote schemas. */
function resolveSchema({ schema }: { schema: Schema }): { schema: Schema; name?: string } {
  if (!schema.$ref) return { schema };
  if (!schema.$ref.startsWith('#/$defs/')) throw new Error('unsupported schema reference');
  const name = schema.$ref.slice('#/$defs/'.length);
  const resolved = definitions[name];
  if (!resolved) throw new Error(`missing SDK schema ${name}`);
  return { schema: { ...resolved, ...schema, $ref: undefined }, name };
}

/** Fill defaults on a copy, retaining strict JSON types and Python extra behavior. */
function normalize({ value, schema, name }: { value: unknown; schema: Schema; name?: string }): unknown {
  const resolved = resolveSchema({ schema });
  schema = resolved.schema;
  name = resolved.name ?? name;
  const variants = schema.anyOf ?? schema.oneOf;
  if (variants) {
    for (const variant of variants) {
      try {
        const candidate = normalize({ value, schema: variant });
        let validate = variantCache.get(variant);
        if (!validate) { validate = ajv.compile({ $defs: schemas.$defs, ...variant }); variantCache.set(variant, validate); }
        if (validate(candidate)) return candidate;
      } catch (error) { if (!(error instanceof InputValidationError)) throw error; }
    }
    fail({ detail: 'value does not match any schema variant' });
  }
  if (Array.isArray(value) && schema.items) return value.map(item => normalize({ value: item, schema: schema.items! }));
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    const fields = schema.properties ?? {};
    const result = Object.fromEntries(Object.entries(value).filter(([key]) => schema['x-extra'] !== 'ignore' || key in fields)
      .map(([key, item]) => [key, normalize({ value: item, schema: fields[key]
        ?? (typeof schema.additionalProperties === 'object' ? schema.additionalProperties : {}) })]));
    for (const [key, field] of Object.entries(fields)) {
      if (!(key in result) && 'default' in field) Object.defineProperty(result, key, {
        value: normalize({ value: structuredClone(field.default), schema: field }),
        enumerable: true, writable: true, configurable: true,
      });
    }
    semanticInvariant({ name, value: result });
    return result;
  }
  return value;
}

/** Reject the cross-field invariants which JSON Schema alone cannot express. */
function semanticInvariant({ name, value }: { name?: string; value: Record<string, unknown> }): void {
  if (name === 'DocumentSearchRequest' && value.query != null && value.cursor != null) fail({ detail: 'query and cursor cannot be combined' });
  if (name === 'OverlapTemporalScope' && Date.parse(String(value.to)) < Date.parse(String(value.from))) fail({ detail: 'temporal scope ends before it starts' });
  if (name === 'EvidenceSpan' && typeof value.char_end === 'number' && typeof value.char_start === 'number'
    && value.char_end <= value.char_start) fail({ detail: 'evidence span end must be after start' });
  if (name === 'EvidenceTotal' && typeof value.returned === 'number' && typeof value.total === 'number'
    && value.returned > value.total) fail({ detail: 'returned evidence exceeds total' });
  if (name === 'ContextBundleV2') {
    const claims = value.claims_and_sources as Record<string, unknown> | undefined;
    const facts = value.facts as Record<string, unknown> | undefined;
    if (claims?.grain !== 'evidence' || facts?.grain !== 'fact') fail({ detail: 'context bundle child grain mismatch' });
  }
  if (name === 'ConnectorCreate' && hasSecret({ value: value.configuration })) fail({ detail: 'connector configuration must reference credentials' });
}

/** Keep invalid-value details independent of secret-bearing customer input. */
function fail({ detail }: { detail: string }): never { throw new InputValidationError({ detail }); }

/** Search nested connector configuration keys using Python's exact normalization. */
function hasSecret({ value }: { value: unknown }): boolean {
  const names = new Set(constants.secretConfigurationKeys);
  if (Array.isArray(value)) return value.some(item => hasSecret({ value: item }));
  if (value !== null && typeof value === 'object') {
    return Object.entries(value).some(([key, item]) => names.has(key.toLowerCase().replace(/[-_]/g, '')) || hasSecret({ value: item }));
  }
  return false;
}

/** Validate and default one named schema; response failures stay HTTP-shaped errors. */
export function validateModel<T>({ name, value, response = false }: { name: string; value: unknown; response?: boolean }): T {
  try {
    assertJson({ value });
    const schema = definitions[name];
    if (!schema) throw new Error(`missing SDK schema ${name}`);
    const result = normalize({ value, schema, name });
    let validate = cache.get(name);
    if (!validate) {
      validate = ajv.getSchema('urn:remember-client:schemas#/$defs/' + name);
      if (!validate) throw new Error(`schema cannot compile: ${name}`);
      cache.set(name, validate);
    }
    if (!validate(result)) fail({ detail: `${name} does not match the API contract` });
    return result as T;
  } catch (error) {
    if (response && error instanceof InputValidationError) throw new MemoryApiError({ statusCode: 200, detail: `${name} response does not match the API contract` });
    throw error;
  }
}

/** Check a closed schema-owned tool object without applying model coercions. */
export function validateSchema({ schema, value }: { schema: object; value: unknown }): void {
  assertJson({ value });
  const validate = ajv.compile(schema);
  if (!validate(value)) fail({ detail: 'tool arguments do not match the catalogue schema' });
}

/** Compare JSON defaults recursively without relying on object key ordering. */
function equalJson({left,right}:{left:unknown;right:unknown}):boolean {
  if(left===right)return true;
  if(Array.isArray(left)&&Array.isArray(right))return left.length===right.length&&left.every((item,index)=>equalJson({left:item,right:right[index]}));
  if(left&&right&&typeof left==='object'&&typeof right==='object') {
    const a=left as Record<string,unknown>,b=right as Record<string,unknown>;
    return Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(key=>key in b&&equalJson({left:a[key],right:b[key]}));
  }
  return false;
}
/** Match Python model_dump's recursive omission of nulls or default-valued fields. */
function dump({value,schema,excludeNone,excludeDefaults}:{value:unknown;schema:Schema;excludeNone:boolean;excludeDefaults:boolean}):unknown {
  schema=resolveSchema({schema}).schema;
  const variants=schema.anyOf??schema.oneOf;
  if(variants) {
    for(const variant of variants) {
      const validate=ajv.compile({$defs:schemas.$defs,...variant});
      if(validate(value))return dump({value,schema:variant,excludeNone,excludeDefaults});
    }
  }
  if(Array.isArray(value))return value.map(item=>dump({value:item,schema:schema.items??{},excludeNone,excludeDefaults}));
  if(value!==null&&typeof value==='object') {
    const result:Record<string,unknown>={};
    for(const [key,item]of Object.entries(value)) {
      const field=schema.properties?.[key]??{};
      if(field['x-exclude'])continue;
      if(excludeNone&&item===null)continue;
      if(excludeDefaults&&'default'in field&&equalJson({left:item,right:normalize({value:field.default,schema:field})}))continue;
      Object.defineProperty(result,key,{value:dump({value:item,schema:field,excludeNone,excludeDefaults}),enumerable:true});
    }
    return result;
  }
  if(typeof value==='string'&&schema.format==='date-time') {
    // Pydantic renders zero offsets as Z and nonzero fractions at microsecond precision.
    return value.replace(/([+]00:00|-00:00)$/,'Z').replace(/\.(\d{1,6})(?=Z|[+-]\d{2}:\d{2}$)/,(_match,fraction:string)=>Number(fraction)===0?'':'.'+fraction.padEnd(6,'0'));
  }
  return value;
}
/** Validate and render nested model inputs from the original source schema. */
export function modelDump({name,value,excludeNone=false,excludeDefaults=false}:{name:string;value:unknown;excludeNone?:boolean;excludeDefaults?:boolean}):Record<string,unknown> {
  return dump({value:validateModel({name,value}),schema:definitions[name]!,excludeNone,excludeDefaults}) as Record<string,unknown>;
}
