/** Repeated public validation must not retain one compiled schema per invocation. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';

test('model dumps and tool calls have bounded retained heap after warmup',()=>{
  const module=new URL('../.test-build/internal.js',import.meta.url).href;
  const script=`import {modelDump,validateArguments} from ${JSON.stringify(module)};
    async function batch(){for(let i=0;i<3000;i++){
      modelDump({name:'DocumentSearchRequest',value:{query:'fixture',filters:{authors:['alice']}}});
      await validateArguments({name:'query_sql',arguments:{sql:'SELECT 1'}});
    }}
    await batch();global.gc();const before=process.memoryUsage().heapUsed;
    await batch();global.gc();console.log(process.memoryUsage().heapUsed-before);`;
  const retained=Number(execFileSync(process.execPath,['--expose-gc','--input-type=module','-e',script],{encoding:'utf8',timeout:60000}));
  assert(Number.isFinite(retained)&&retained<4_000_000,`repeated validation retained ${retained} bytes`);
});
