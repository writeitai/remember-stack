/** Compare every validator and error branch with executed Python observations. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {validateArguments,MemoryApiError,RateLimited,TimeoutError,mapError,ToolArgumentError} from '../dist/index.js';
const fixture=JSON.parse(readFileSync(new URL('./fixtures/python-methods.json',import.meta.url)));

/** Render parsed binary bodies into the fixture's deliberate base64 representation. */
function render(value){
  if(value instanceof Uint8Array)return {base64:Buffer.from(value).toString('base64')};
  if(Array.isArray(value))return value.map(render);
  if(value!==null&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,render(item)]));
  return value;
}
for(const scenario of fixture.tools)test(`${scenario.name}: source validator ${scenario.variant}`,async()=>{
  if(scenario.error)await assert.rejects(validateArguments({name:scenario.name,arguments:scenario.arguments}),error=>{
    assert.equal(error.name,scenario.error.class);
    const actual=error instanceof ToolArgumentError?error.error:error;
    assert.equal(actual.code,scenario.error.code);if('statusCode'in scenario.error)assert.equal(actual.status_code??null,scenario.error.statusCode);
    if('retryable'in scenario.error)assert.equal(actual.retryable,scenario.error.retryable);
    const mapped=mapError({error}).asDict();
    // Language validator wording differs; all machine fields and prescribed actions match.
    assert.deepEqual({...mapped.error,detail:'normalized'}, {...scenario.mapped.error,detail:'normalized'});
    assert(mapped.error.detail.length>0);return true;
  });
  else assert.deepEqual(render(await validateArguments({name:scenario.name,arguments:scenario.arguments})),scenario.result);
});
for(const scenario of fixture.errorMappings)test(`source error mapping ${scenario.options.statusCode}/${scenario.options.code??scenario.options.detail}`,()=>{
  const options=Object.fromEntries(Object.entries(scenario.options).filter(([,value])=>value!==null));
  const error=scenario.class==='TimeoutError'?new TimeoutError():scenario.class==='RateLimited'?new RateLimited(options):new MemoryApiError(options);
  assert.deepEqual(mapError({error}).asDict(),scenario.result);
});
