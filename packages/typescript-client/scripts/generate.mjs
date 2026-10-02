/** Generate models through the pinned type-only OpenAPI projection. */
import {readFile,writeFile,mkdir,mkdtemp,readdir,rm} from 'node:fs/promises';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {generate} from 'openapi-typescript-codegen';
import ts from 'typescript';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

/** Project the used 2020-12 constructs; refuse unsupported lossy projections.
 * @param {{value: any}} options
 * @returns {any}
 */
export function project({value}) {
  if (Array.isArray(value)) return value.map(item => project({value:item}));
  if (value === null || typeof value !== 'object') return value;
  for (const keyword of ['prefixItems','unevaluatedProperties','unevaluatedItems','dependentSchemas','dependentRequired']) {
    if (keyword in value) throw new Error(`Unsupported type projection: ${keyword}`);
  }
  const result = Object.fromEntries(Object.entries(value).filter(([key]) => !key.startsWith('x-')).map(([key,item]) => [key,project({value:item})]));
  if ('const' in result) {result.enum=[result.const];delete result.const;}
  if (result.type === 'null') {delete result.type;result.enum=[null];result.nullable=true;}
  if (Array.isArray(result.type)) {
    const nonnull=result.type.filter(type=>type!=='null');
    result.nullable=result.type.includes('null');
    if(nonnull.length!==1) throw new Error('Unsupported multi-type projection');
    result.type=nonnull[0];
  }
  if (Array.isArray(result.anyOf) && result.anyOf.some(item=> item.enum?.length===1 && item.enum[0]===null)) {
    result.nullable=true;
    const nonnull=result.anyOf.filter(item=> !(item.enum?.length===1 && item.enum[0]===null));
    delete result.anyOf;
    if(nonnull.length===1) Object.assign(result,nonnull[0]);
    else result.anyOf=nonnull;
  }
  if(result.contentMediaType && result.type==='string') {result.format='binary';delete result.contentMediaType;}
  delete result.$schema;
  return result;
}

/** Repair exact-null types the 3.0 generator widens to any, from source schemas.
 * @param {{content: string, schema: any, name: string}} options
 * @returns {string}
 */
function exactNulls({content,schema,name}) {
  if (!schema) return content;
  const nullProperties=new Set(Object.entries(schema.properties ?? {}).filter(([,value])=>value.type==='null').map(([key])=>key));
  const recursive=JSON.stringify(schema).includes('#/components/schemas/'+name);
  const hasAny = /\bany\b/.test(content);
  if(schema.type!=='null' && nullProperties.size===0 && !recursive && !hasAny) return content;
  const source=ts.createSourceFile(name+'.ts',content,ts.ScriptTarget.Latest,true);
  const result=ts.transform(source,[context=>{
    /** Visit the named model's source-owned exact-null/recursive properties.
     * @param {import("typescript").Node} node
     * @returns {import("typescript").VisitResult<import("typescript").Node>}
     */
    function visit(node) {
      if(node.kind===ts.SyntaxKind.AnyKeyword) return ts.factory.createTypeReferenceNode('JsonValue');
      if(ts.isTypeReferenceNode(node) && ts.isIdentifier(node.typeName) && node.typeName.text==='Record'
        && node.typeArguments?.length===2 && ts.isTypeReferenceNode(node.typeArguments[1])
        && node.typeArguments[1].typeName.getText(source)===name) {
        return ts.factory.createTypeLiteralNode([ts.factory.createIndexSignature(undefined,[
          ts.factory.createParameterDeclaration(undefined,undefined,'key',undefined,
            ts.factory.createKeywordTypeNode(ts.SyntaxKind.StringKeyword))
        ],node.typeArguments[1])]);
      }
      if(ts.isTypeAliasDeclaration(node) && node.name.text===name) {
        if(schema.type==='null') return ts.factory.updateTypeAliasDeclaration(node,node.modifiers,node.name,node.typeParameters,ts.factory.createLiteralTypeNode(ts.factory.createNull()));
        if(nullProperties.size===0) return ts.visitEachChild(node,visit,context);
        if(!ts.isTypeLiteralNode(node.type)) throw new Error('Null property in non-object model');
        const members=node.type.members.map(member=> {
          if(ts.isPropertySignature(member) && nullProperties.has(member.name.getText(source).replace(/^['"]|['"]$/g,''))) {
            return ts.factory.updatePropertySignature(member,member.modifiers,member.name,member.questionToken,ts.factory.createLiteralTypeNode(ts.factory.createNull()));
          }
          return ts.visitEachChild(member,visit,context);
        });
        return ts.factory.updateTypeAliasDeclaration(node,node.modifiers,node.name,node.typeParameters,ts.factory.updateTypeLiteralNode(node.type,members));
      }
      return ts.visitEachChild(node,visit,context);
    }
    return node=>ts.visitNode(node,visit);
  }]);
  try {
    const printed=ts.createPrinter().printFile(result.transformed[0]);
    return hasAny && !/^import type \{ JsonValue \}/m.test(printed) && name!=='JsonValue' ? "import type { JsonValue } from './JsonValue';\n"+printed : printed;
  }
  finally {result.dispose();}
}

/** Recursively list deterministic generated output paths.
 * @param {{directory: string, prefix?: string}} options
 * @returns {Promise<string[]>}
 */
async function files({directory,prefix=''}) {
  const entries=await readdir(directory,{withFileTypes:true});
  const result=[];
  for(const entry of entries.sort((a,b)=>a.name.localeCompare(b.name))) {
    const name=join(prefix,entry.name).replaceAll('\\','/');
    if(entry.isDirectory()) result.push(...await files({directory:join(directory,entry.name),prefix:name}));
    else result.push(name);
  }
  return result;
}

/** Generate to a temporary tree; check never edits the committed client.
 * @returns {Promise<void>}
 */
async function main() {
  const check=process.argv.includes('--check');
  const temporary=await mkdtemp(join(root,'.generation-'));
  try {
    const input=JSON.parse(await readFile(join(root,'contracts/openapi-types.json'),'utf8'));
    const projected=project({value:input});projected.openapi='3.0.3';
    await generate({input:projected,output:temporary,exportCore:false,exportServices:false,exportModels:true,useUnionTypes:true,indent:'4'});
    const generated=join(root,'src/generated');
    const names=await files({directory:temporary});
    for (const name of names) {
      let content=await readFile(join(temporary,name),'utf8');
      const modelName=name.replace(/^models[/\\]/,'').replace(/\.ts$/,'');
      if(name.startsWith('models/')) content=exactNulls({content,schema:input.components.schemas[modelName],name:modelName});
      const destination=join(generated,name);
      if(check) {
        const committed=await readFile(destination,'utf8').catch(()=>null);
        if(committed!==content) throw new Error(`Generated TypeScript drift: ${name}`);
      } else {await mkdir(dirname(destination),{recursive:true});await writeFile(destination,content);}
    }
    const old=await files({directory:generated});
    for(const name of old) {
      if(!names.includes(name)) {
        if(check) throw new Error(`Obsolete generated TypeScript: ${name}`);
        await rm(join(generated,name));
      }
    }
    const publicTypes = Object.keys(input.components.schemas).filter(name=>name.startsWith('Output') && !['OutputKeyClaims','OutputIssuerMetadata','OutputStoredCredentials','OutputJsonValue','OutputResolvedProject'].includes(name)).sort().map(name=>`export type { ${name} as ${name.slice(6)}, ${name.slice(6)} as ${name.slice(6)}Input } from './generated';`).join('\n') + "\nexport type { JsonValue } from './generated';\n";
    const publicTypesPath=join(root,'src/public-types.generated.ts');
    if(check) {if(await readFile(publicTypesPath,'utf8')!==publicTypes) throw new Error('Public model export drift');}
    else await writeFile(publicTypesPath,publicTypes);
    const packageMetadata=JSON.parse(await readFile(join(root,'package.json'),'utf8'));
    const compatibility=JSON.parse(await readFile(join(root,'contracts/metadata.json'),'utf8'));
    const versionContent='/* Generated package version; regenerate with npm run generate. */\nexport const version = '+JSON.stringify(packageMetadata.version)+';\nexport const pythonCompatibility = '+JSON.stringify(compatibility)+' as const;\n';
    const versionPath=join(root,'src/version.generated.ts');
    if(check) {if(await readFile(versionPath,'utf8')!==versionContent)throw new Error('Package version export drift');}
    else await writeFile(versionPath,versionContent);
    const contracts=['schemas','constants','catalogue','metadata','routes','parity'];
    for (const name of contracts) {
      const content='/* Generated from Python; regenerate with npm run generate. */\nexport default '+(await readFile(join(root,`contracts/${name}.json`),'utf8')).trim()+';\n';
      const destination=join(root,`src/${name}.generated.ts`);
      if(check) {if(await readFile(destination,'utf8')!==content) throw new Error(`Contract module drift: ${name}`);}
      else await writeFile(destination,content);
    }
  } finally {await rm(temporary,{recursive:true,force:true});}
}

if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  try{await main();}
  catch(error){process.stderr.write((error instanceof Error?error.message:'TypeScript generation failed')+'\n');process.exitCode=1;}
}
