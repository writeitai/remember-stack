/** Check native SDK clocks against each release's actual FastAPI validators. */
import assert from 'node:assert/strict';
import {Client,MemoryApiError} from '../dist/index.js';
const version=process.env.TARGET_ENGINE_VERSION;
const baseUrl='http://127.0.0.1:8001';
const client=new Client({apiKey:'test-compatibility-key',baseUrl});
const id='10000000-0000-0000-0000-000000000001';
const expected=[];
const outcomes=[];
try {
  for(const [method,base]of [['lookupRelations',{}],['graphNeighborhood',{entityId:id}],['graphPath',{fromEntityId:id,toEntityId:id}]]) {
    for(const validAt of ['2026-01-01T12:00:00Z','2026-01-01T12:00:00+02:00']) {
      const options={...base,validAt,...(method==='lookupRelations'?{}:{believedAt:validAt})};
      await assert.rejects(client[method](options),error=>{
        assert(error instanceof MemoryApiError,`${method} must retain the ordinary HTTP error type`);
        assert.equal(error.statusCode,418);
        assert.equal(error.detail,'temporal-boundary-reached');
        return true;
      });
      const utc=new Date(validAt).toISOString().replace('.000Z','+00:00');
      expected.push({method,valid_at:utc,believed_at:method==='lookupRelations'?null:utc});
      outcomes.push({method,validAt,outcome:'boundary-accepted'});
    }
  }
  const response=await fetch(baseUrl+'/fixture/recordings');assert(response.ok);
  assert.deepEqual(await response.json(),expected,'Each engine receives the equivalent UTC instants');
  console.log(JSON.stringify({engine:version,temporalBoundary:outcomes},null,2));
}finally{client.close();}
