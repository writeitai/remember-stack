/** Verify D140 precision, strictness, recursive defaults and cross-field rules. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseJson,assertJson,validateModel,InputValidationError,MemoryApiError,NumericPrecisionError} from '../.test-build/internal.js';
import {project} from '../scripts/generate.mjs';

const id='10000000-0000-0000-0000-000000000001';

test('JSON precision keeps safe boundaries and rejects unsafe original tokens',()=>{
  assert.equal(parseJson({text:'9007199254740991'}),Number.MAX_SAFE_INTEGER);
  assert.equal(parseJson({text:'-9007199254740991'}),Number.MIN_SAFE_INTEGER);
  for(const text of ['9007199254740993','-9007199254740993','1e400','{"nested":[9007199254740993]}']) {
    assert.throws(()=>parseJson({text}),NumericPrecisionError);
  }
  assert.throws(()=>assertJson({value:{parameters:[2**53]}}),error=>error instanceof InputValidationError && error.code==='numeric.precision');
  assert.doesNotThrow(()=>assertJson({value:{parameters:['9007199254740993']}}));
  const cycle={};cycle.self=cycle;
  assert.throws(()=>assertJson({value:cycle}),InputValidationError);
});

test('defaults recurse through optional model fields without coercion',()=>{
  const result=validateModel({name:'DocumentSearchRequest',value:{}});
  assert.equal(result.versions,'current');
  assert.equal(result.k,20);
  assert.equal(result.query,null);
  assert.equal(result.cursor,null);
  assert.deepEqual(result.filters.authors,[]);
  assert.deepEqual(result.filters.doc_ids,[]);
  assert.equal(result.filters.language,null);
  assert.throws(()=>validateModel({name:'DocumentSearchRequest',value:{k:'20'}}),InputValidationError);
  assert.throws(()=>validateModel({name:'DocumentSearchRequest',value:{k:true}}),InputValidationError);
  assert.throws(()=>validateModel({name:'DocumentSearchRequest',value:{query:'text',cursor:'next'}}),InputValidationError);
  assert.throws(()=>validateModel({name:'DocumentSearchRequest',value:{unknown:'value'},response:true}),MemoryApiError);
});

test('cross-field semantic bounds match Python models',()=>{
  assert.throws(()=>validateModel({name:'EvidenceSpan',value:{char_start:2,char_end:2}}),InputValidationError);
  assert.deepEqual(validateModel({name:'EvidenceSpan',value:{char_start:2,char_end:3}}),{char_start:2,char_end:3});
  assert.throws(()=>validateModel({name:'EvidenceTotal',value:{fact_kind:'relation',fact_id:id,stance:'supports',returned:3,total:2}}),InputValidationError);
  assert.throws(()=>validateModel({name:'OverlapTemporalScope',value:{from:'2026-10-02T00:00:00Z',to:'2026-10-01T00:00:00Z',evaluated_at:'2026-10-02T00:00:00Z',believed_at:'2026-10-02T00:00:00Z'}}),InputValidationError);
});

test('source-owned UTC annotation refuses nonzero offsets',()=>{
  const scope={mode:'overlap',from:'2026-10-01T00:00:00Z',to:'2026-10-02T00:00:00Z',evaluated_at:'2026-10-02T00:00:00Z',believed_at:'2026-10-02T00:00:00Z'};
  assert.doesNotThrow(()=>validateModel({name:'OverlapTemporalScope',value:scope}));
  assert.throws(()=>validateModel({name:'OverlapTemporalScope',value:{...scope,believed_at:'2026-10-02T00:00:00+01:00'}}),InputValidationError);
});

test('nested connector secrets are rejected, references accepted',()=>{
  assert.throws(()=>validateModel({name:'ConnectorCreate',value:{kind:'custom',name:'example',configuration:{nested:[{'api-key':'not-a-real-secret'}]}}}),InputValidationError);
  assert.doesNotThrow(()=>validateModel({name:'ConnectorCreate',value:{kind:'custom',name:'example',configuration:{endpoint:'https://example.com'},credential_ref:'operator-ref'}}));
});

test('projection refuses unsupported constructs',()=>{
  assert.deepEqual(project({value:{type:'string',const:'literal'}}),{type:'string',enum:['literal']});
  assert.deepEqual(project({value:{anyOf:[{type:'string'},{type:'null'}]}}),{type:'string',nullable:true});
  assert.throws(()=>project({value:{prefixItems:[{type:'string'}]}}),/Unsupported/);
});
