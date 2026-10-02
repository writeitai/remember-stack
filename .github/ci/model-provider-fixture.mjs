/** Loopback-only model fixture for API compatibility, never an external inference call. */
import {createServer} from 'node:http';
/** Return deterministic unit vectors with the request's model/dimension and valid accounting.
 * @param {import('node:http').IncomingMessage} request
 * @param {import('node:http').ServerResponse} response
 * @returns {Promise<void>}
 */
async function serve(request,response) {
  if(request.method==='GET'&&request.url==='/healthz'){response.end('fixture');return;}
  if(request.method!=='POST'||request.url!=='/embeddings'){response.writeHead(501);response.end('unexpected model fixture call');return;}
  const chunks=[];for await(const chunk of request)chunks.push(chunk);
  const body=JSON.parse(Buffer.concat(chunks).toString('utf8'));
  if(typeof body.model!=='string'||!Array.isArray(body.input)||!Number.isInteger(body.dimensions)||body.dimensions<=0||body.dimensions>10000){response.writeHead(400);response.end('invalid fixture embedding request');return;}
  const vector=Array(body.dimensions).fill(0);vector[0]=1;
  response.writeHead(200,{'content-type':'application/json'});
  response.end(JSON.stringify({model:body.model,usage:{prompt_tokens:body.input.length,cost:'0'},data:body.input.map((_text,index)=>({index,embedding:vector}))}));
}
const server=createServer((request,response)=>{serve(request,response).catch(()=>{response.writeHead(500);response.end('model fixture failure');});});
server.listen(18882,'127.0.0.1');
process.on('SIGTERM',()=>{server.closeAllConnections();server.close();});
