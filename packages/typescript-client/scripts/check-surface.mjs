/** Check every reviewed public export, support member and non-wire Python default. */
import ts from 'typescript';
import {readFileSync} from 'node:fs';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=process.argv[2]??resolve(dirname(fileURLToPath(import.meta.url)),'..');
const inventory=JSON.parse(readFileSync(resolve(root,'contracts/parity.json'),'utf8'));
const program=ts.createProgram([resolve(root,'src/index.ts')],{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,moduleResolution:ts.ModuleResolutionKind.Bundler,esModuleInterop:true,skipLibCheck:true});
const checker=program.getTypeChecker();
const index=program.getSourceFile(resolve(root,'src/index.ts'));
const exports=new Map(checker.getExportsOfModule(checker.getSymbolAtLocation(index)).map(symbol=>[symbol.name,symbol]));
/** Convert only source-owned top-level names, never wire DTO field names.
 * @param {string} name
 * @returns {string}
 */
function camel(name){return name.replace(/_([a-z])/g,(_,letter)=>letter.toUpperCase());}
/** Refuse missing public declarations, including types erased at runtime.
 * @param {{name:string}} options
 * @returns {import('typescript').Symbol}
 */
function exported({name}){const value=exports.get(name);if(!value)throw new Error(`Missing reviewed public export: ${name}`);return value.flags&ts.SymbolFlags.Alias?checker.getAliasedSymbol(value):value;}
for(const name of inventory.exports)exported({name:name==='__version__'?'version':camel(name)});
for(const definition of Object.values(inventory.supportExports.functions))exported({name:definition.typescript});
for(const definition of Object.values(inventory.supportExports.memoryToolCatalogue.exports)){
  if(definition.typescript)exported({name:definition.typescript});
  else if(!definition.disposition.startsWith('separate MCP library:'))throw new Error('Unreviewed export disposition');
}
for(const definition of Object.values(inventory.supportExports.types)){
  const symbol=exported({name:definition.typescript});const type=checker.getDeclaredTypeOfSymbol(symbol);
  for(const name of Object.keys(definition.fields)){const field=['Connection','ToolDefinition'].includes(definition.typescript)?camel(name):name;if(!type.getProperty(field))throw new Error(`Missing support field ${definition.typescript}.${field}`);}
  for(const [name,method]of Object.entries(definition.methods))if(name!=='__init__'&&!type.getProperty(method.typescript))throw new Error(`Missing support method ${definition.typescript}.${method.typescript}`);
}
for(const [className,methods]of Object.entries(inventory.classes)){
  const symbol=exported({name:className});const type=checker.getDeclaredTypeOfSymbol(symbol);
  for(const name of Object.keys(methods)){
    if(name==='__init__')continue;
    const member=camel(name);
    const target=name==='from_env'?checker.getTypeOfSymbolAtLocation(symbol,index):type;
    if(!target.getProperty(member))throw new Error(`Missing public member ${className}.${member}`);
  }
}
const client=program.getSourceFile(resolve(root,'src/client.ts'));
const memory=client.statements.find(node=>ts.isClassDeclaration(node)&&node.name.text==='MemoryClient');
/** Compare an executable binding default with the Python default converted to milliseconds.
 * @param {{method:string,parameter:string,pythonParameter:string}} options
 * @returns {void}
 */
function millisecondsDefault({method,parameter,pythonParameter}){
  const signature=inventory.classes.MemoryClient[method].arguments;
  const match=new RegExp(`\\b${pythonParameter}: [^=,]+=(\\d+(?:\\.\\d+)?)`).exec(signature);
  if(!match)throw new Error(`Python ${method}.${pythonParameter} default requires review`);
  const node=memory.members.find(member=>method==='__init__'?ts.isConstructorDeclaration(member):member.name?.getText(client)===camel(method));
  const binding=node.parameters[0].name.elements.find(element=>element.name.getText(client)===parameter);
  if(!binding?.initializer||!ts.isNumericLiteral(binding.initializer)||Number(binding.initializer.text)!==Number(match[1])*1000)throw new Error(`Python/TypeScript default drift: ${method}.${parameter}`);
}
millisecondsDefault({method:'__init__',parameter:'timeoutMs',pythonParameter:'timeout'});
millisecondsDefault({method:'wait_for_readiness',parameter:'timeoutMs',pythonParameter:'timeout'});
millisecondsDefault({method:'wait_for_readiness',parameter:'pollIntervalMs',pythonParameter:'poll_interval'});
console.log('Reviewed root/support exports, fields, methods and non-wire defaults match');
