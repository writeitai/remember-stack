/** Cross-check actual Python/TS wire fixtures against the original engine OpenAPI. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
const api=JSON.parse(readFileSync(new URL('../../../openapi.json',import.meta.url)));
const fixtures=JSON.parse(readFileSync(new URL('./fixtures/python-methods.json',import.meta.url)));
const routes=JSON.parse(readFileSync(new URL('../contracts/routes.json',import.meta.url)));
const ajv=new Ajv2020({strict:false,allErrors:true});addFormats(ajv);
// AJV otherwise treats this OpenAPI keyword as an annotation. Server-side
// Pydantic discriminated unions require an explicitly supplied, known tag.
ajv.addKeyword({keyword:'discriminator',schemaType:'object',validate:(schema,value)=>value!==null&&typeof value==='object'&&typeof value[schema.propertyName]==='string'&&Object.hasOwn(schema.mapping,value[schema.propertyName])});

/** Validate original schema references without using the type generator's projection. */
function validate({schema,value,label}) {
  const check=ajv.compile({components:api.components,...schema});
  assert(check(value),`${label}: ${JSON.stringify(check.errors)}`);
}
/** Find one unambiguous method/path from the authoritative public document. */
function route({request}) {
  const matches=[];
  for(const [path,methods]of Object.entries(api.paths)) {
    const operation=methods[request.method.toLowerCase()];if(!operation)continue;
    const names=[];const pattern='^'+path.replace(/\{([^}]+)\}/g,(_,name)=>{names.push(name);return '([^/]+)';})+'$';
    const match=new RegExp(pattern).exec(request.path);if(match)matches.push({path,operation,names,match});
  }
  if(matches.length===0) {
    const optional=Object.values(routes).some(candidate=>candidate.optionalProfile==='connectors'&&candidate.method===request.method&&new RegExp('^'+candidate.path.replace(/\{[^}]+\}/g,'[^/]+')+'$').test(request.path));
    assert(optional,`${request.method} ${request.path} is missing from the public API and optional profiles`);return null;
  }
  assert.equal(matches.length,1,'ambiguous API route');return matches[0];
}
/** Decode wire query primitives solely according to their declared API type. */
function primitive({schema,value}) {
  const choices=schema.anyOf??schema.oneOf??[schema];
  if(choices.some(choice=>choice.type==='integer'||choice.type==='number'))return Number(value);
  if(choices.some(choice=>choice.type==='boolean'))return value==='true'?true:value==='false'?false:value;
  return value;
}
for(const scenario of fixtures.cases)test(`${scenario.typescriptMethod}/${scenario.variant}: original API request and response`,()=>{
  for(const [index,request]of scenario.wire.entries()) {
    const found=route({request});if(!found)continue;
    const {path,operation,names,match}=found;
    const label=`${request.method} ${path}`;
    for(const parameter of operation.parameters??[]) {
      let value;
      if(parameter.in==='path')value=decodeURIComponent(match[names.indexOf(parameter.name)+1]);
      else if(parameter.in==='query') {
        const values=request.query.filter(([name])=>name===parameter.name).map(([,value])=>value);
        if(!values.length) {assert(!parameter.required,`${label}: missing ${parameter.name}`);continue;}
        const arraySchema=(parameter.schema.anyOf??parameter.schema.oneOf??[parameter.schema]).find(choice=>choice.type==='array');
        value=arraySchema?values.map(value=>primitive({schema:arraySchema.items,value})):primitive({schema:parameter.schema,value:values[0]});
      }else continue;
      validate({schema:parameter.schema,value,label:`${label} ${parameter.name}`});
    }
    const requestSchema=operation.requestBody?.content?.[request.contentType]?.schema;
    if(operation.requestBody?.required)assert(request.body!==null||request.contentBase64!==null,`${label}: missing body`);
    if(requestSchema&&request.contentType==='application/json')validate({schema:requestSchema,value:request.body,label:`${label} request`});
    const responseSchema=operation.responses?.['200']?.content?.['application/json']?.schema;
    assert(responseSchema,`${label} lacks a successful JSON response contract`);
    validate({schema:responseSchema,value:scenario.responses[index],label:`${label} response`});
  }
});


test('the original API gate rejects a discriminator omitted as a default',()=>{
  const schema=api.paths['/documents/search'].post.requestBody.content['application/json'].schema;
  for(const time of [{},{at:'2026-01-01T00:00:00Z'},{from:'2026-01-01T00:00:00Z',to:'2026-01-01T00:00:00Z'}])assert.throws(()=>validate({schema,value:{time},label:'counterfactual missing mode'}));
});
