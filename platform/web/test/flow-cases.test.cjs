const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const Module=require('node:module');
const {buildSync}=require('esbuild');

const filename=path.resolve(__dirname,'../src/workspace/flowCases.ts');
const compiled=buildSync({entryPoints:[filename],bundle:true,write:false,format:'cjs',platform:'node'});
const loaded=new Module(filename);loaded._compile(compiled.outputFiles[0].text,filename);
const {conditionHolds,casePath,validCases}=loaded.exports;

test('case conditions follow the closed server conjunction grammar and validate every assignment',()=>{
 assert.equal(conditionHolds(null,{}),true);
 assert.equal(conditionHolds('cond:eligible & !cond:preference',{eligible:true,preference:false}),true);
 assert.equal(conditionHolds('cond:eligible & !cond:preference',{eligible:false,preference:false}),false);
 assert.equal(conditionHolds('!cond:eligible',{eligible:false}),true);
 for(const value of ['', 'true', 'cond:a || cond:b', 'cond:a && cond:b', '!!cond:a', 'cond:a.constructor', 'process.exit()']){
  assert.throws(()=>conditionHolds(value,{a:true,b:true}),/when grammar/);
 }
 for(const values of [{a:false},{a:true,b:'false'},{a:true,b:0}]){
  assert.throws(()=>conditionHolds('cond:a & cond:b',values),/missing condition/);
 }
});

test('condition cases must be the complete unique boolean product',()=>{
 assert(validCases([{}]));
 assert(validCases([{a:true,b:true},{a:true,b:false},{a:false,b:true},{a:false,b:false}]));
 for(const value of [[],null,[{a:true}],[{a:true},{a:true}],[{a:true},{b:false}],[{a:'true'},{a:false}]]){
  assert.equal(validCases(value),false);
 }
});

test('tracing selects the actual eligible or rejected branch and ignores retained back navigation',()=>{
 const links=[
  {id:'eligible',from:'entry',to:'terms',condition:'cond:eligible'},
  {id:'rejected',from:'entry',to:'declined',condition:'!cond:eligible'},
  {id:'agreed',from:'terms',to:'complete',condition:'cond:agreed'},
  {id:'back',from:'terms',to:'entry',navigation:'back'},
 ];
 assert.deepEqual(casePath('entry',['complete','declined'],links,{eligible:true,agreed:true}),
   {screens:['entry','terms','complete'],transitions:['eligible','agreed'],end:'terminal'});
 assert.deepEqual(casePath('entry',['complete','declined'],links,{eligible:false,agreed:false}),
   {screens:['entry','declined'],transitions:['rejected'],end:'terminal'});
 assert.equal(casePath('entry',['complete'],links,{eligible:true,agreed:false}).end,'dead-end');
 assert.equal(casePath('entry',['complete'],links,{eligible:true}).end,'ambiguous');
});

test('ambiguous, absent, malformed and cyclic routes stay visibly incomplete',()=>{
 assert.equal(casePath(undefined,[],[],{}).end,'dead-end');
 assert.equal(casePath('a',['z'],[{id:'one',from:'a',to:'b'},{id:'two',from:'a',to:'c'}],{}).end,'ambiguous');
 assert.equal(casePath('a',['z'],[{id:'one',from:'a',to:'b',condition:'bad expression'}],{}).end,'ambiguous');
 assert.deepEqual(casePath('a',['z'],[{id:'one',from:'a',to:'b'},{id:'two',from:'b',to:'a'}],{}),
   {screens:['a','b','a'],transitions:['one','two'],end:'loop'});
 const links=Array.from({length:60},(_,i)=>({id:'t'+i,from:'s'+i,to:'s'+(i+1)}));
 const stopped=casePath('s0',['s60'],links,{});
 assert.equal(stopped.end,'loop');assert.equal(stopped.transitions.length,50);
});
