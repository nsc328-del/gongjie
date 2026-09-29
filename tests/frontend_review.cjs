const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
function between(start, end) { return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start))); }

test('API preserves JSON headers and surfaces input validation errors', async () => {
  let options;
  const context = vm.createContext({state:{spaceId:"owner"}, URL, window:{location:{href:"https://example.test/gongjie/"}}, fetch: async (path, init) => {
    options = init;
    return {ok:false, json:async () => ({detail:[{loc:['body','name'],type:'string_too_short',msg:'不能为空'}]})};
  }});
  vm.runInContext(between('function appPath(', 'async function readStream('), context);
  await assert.rejects(context.api('/test', {headers:{'X-Test':'yes'}}), /名称不能为空/);
  assert.equal(options.headers['Content-Type'], 'application/json');
  assert.equal(options.headers['X-Test'], 'yes');
  assert.equal(options.headers['X-Gongjie-Space'], 'owner');
});

test('stream decodes split Chinese bytes and distinguishes incomplete EOF', async () => {
  const context = vm.createContext({TextDecoder, Uint8Array});
  vm.runInContext(between('async function readStream(', 'function toast('), context);
  const bytes = new TextEncoder().encode('{"type":"delta","content":"回应"}\n{"type":"done"}\n');
  const events = [];
  await context.readStream(new Response(new ReadableStream({start(c) {
    for (const byte of bytes) c.enqueue(new Uint8Array([byte])); c.close();
  }})), event => events.push(event));
  assert.equal(events[0].content, '回应');
  await assert.rejects(context.readStream(new Response('{"type":"delta"}\n'), () => {}), /回应连接中断/);
});

test('room input and writing indicator recover even when final history refresh fails', async () => {
  const state = {room:{room:{id:'r'},characters:[]}, world:{world:{id:'w'}},liveRooms:new Map(),roomDrafts:new Map(),recovering:false};
  const input = {value:'你好'};
  const context = vm.createContext({state, TextDecoder, Uint8Array, appPath: path => path,
    $:() => input, worldBusy:() => false, pendingSettlements:() => [],
    setActivity:() => {}, renderRoom:() => {}, renderCurrent:() => {}, toast:() => {},
    api:async () => {throw new Error('网络中断');}, refreshWorld:async () => {},
    appFetch:async () => {
      assert.equal(state.liveRooms.get('r').messages.get('pending-user').content, '你好');
      return new Response('{"type":"user","message":{"id":"u"}}\n{"type":"room_reply","message":{"id":"a","content":"回应"}}\n');
    },
  });
  vm.runInContext(between('async function readStream(', 'function toast(')+between('async function sendRoomTurn(', 'function characterCard('), context);
  await context.sendRoomTurn();
  assert.equal(state.liveRooms.get('r').finished, true);
  assert.equal(state.liveRooms.get('r').messages.get('a').writing, false);
  assert.equal(state.roomStreaming, false);
  assert.equal(state.roomWriting, false);
  assert.equal(state.liveRooms.get('r').messages.has('pending-user'), false);
});

test('outdated preview responses and failures cannot replace a refreshed world', async () => {
  const state={world:{world:{id:'w'},time_preview:null},view:'chat',conversation:{needs_scene_entry:false,messages:[{}]}};
  let resolve, reject, busy=false, repaints=0;
  const slot={innerHTML:''}, input={value:'正在输入一半的句子'};
  const context=vm.createContext({state,worldBusy:()=>busy,pendingSettlements:()=>[],escapeHTML:x=>x,
    $:selector=>selector==='.time-preview-slot'?slot:selector==='#advance-turn'?{}:input,
    renderCurrent:()=>{throw Error('a preview must not rebuild the chat');},
    api:path=>path.endsWith('time-preview')?new Promise((yes,no)=>{resolve=yes;reject=no;}):Promise.resolve({time_preview:{id:'old',world_time:'旧时刻'}}),
  });
  vm.runInContext(between('const previewRequests =', 'async function openSaves('),context);
  const pending=context.ensureTimePreview();
  context.invalidateTimePreview('w');
  state.world.time_preview={id:'fresh',world_time:'新时刻'};
  resolve({id:'old',world_time:'旧时刻'});await pending;
  assert.equal(state.world.time_preview.id,'fresh');
  assert.equal(slot.innerHTML,'');
  assert.equal(input.value,'正在输入一半的句子');
  context.invalidateTimePreview('w');
  const failed=context.ensureTimePreview();
  context.invalidateTimePreview('w');busy=true;
  reject(Error('旧请求失败'));await failed;
  assert.equal(vm.runInContext('previewErrors.has("w")',context),false);
});

test('save dialog serializes writes and loading clears room drafts as well', async () => {
  const state={world:{world:{id:'w'}},liveTurns:new Map(),liveRooms:new Map(),viewMemory:new Map(),conversationDrafts:new Map(),roomDrafts:new Map([['r','旧草稿']])};
  const dialog={dataset:{},open:true}, create={}, list={}, load={dataset:{loadSave:'s'}};
  let release, writes=0, opens=0, busy=false;
  const context=vm.createContext({state,escapeHTML:x=>x,Date,
    $:selector=>({'#saves-dialog':dialog,'#create-save':create,'#save-list':list}[selector]),
    $$:()=>[create,load],worldBusy:()=>busy,pendingSettlements:()=>[],
    setActivity:(_id,label)=>{busy=!!label;},renderCurrent:()=>{},closeDialog:()=>{},showDialog:()=>{},toast:()=>{},
    openWorld:async()=>{opens++;},
    api:async(path,options)=>{
      if(!options) return [{id:'s',name:'存档',world_time:'钟响前',created_at:'2026-09-10T00:00:00Z'}];
      writes++;await new Promise(resolve=>{release=resolve;});return {};
    },
  });
  vm.runInContext(between('async function openSaves(', 'async function advanceWorld('),context);
  await context.openSaves();
  const event={target:{closest:()=>load}};
  const first=list.onclick(event);
  await list.onclick(event);await create.onclick();
  assert.equal(writes,1);assert.equal(dialog.dataset.busy,'true');
  release();await first;
  assert.equal(state.roomDrafts.size,0);assert.equal(opens,1);assert.equal(busy,false);
});

test('world cards do not keep distant or unavailable people marked present', () => {
  const context = vm.createContext({state:{}});
  vm.runInContext(between('function effectiveAvailability(', 'function roomCard('), context);
  assert.equal(context.effectiveAvailability({availability:'present',current_location_id:'other'}, {location_id:'here'}), 'reachable');
  assert.equal(context.effectiveAvailability({availability:'unavailable',current_location_id:'here'}, {location_id:'here'}), 'unavailable');
  assert.equal(context.effectiveAvailability({availability:'reachable',current_location_id:'here'}, {location_id:'here'}), 'present');
});

test('character cards stop reusing the initial hook after interaction or a world pulse', () => {
  const character={id:'c',name:'角色',opening_hook:'授权书尚未签署',encounter_hook:'授权书尚未签署',state_summary:'站在窗边',has_messages:true,pulse_number:0};
  const context=vm.createContext({state:{world:{player:{}}},relationshipFor:()=>({notes:'已签署授权'}),effectiveAvailability:()=> 'reachable',escapeHTML:x=>x||'',avatar:()=>''});
  vm.runInContext(between('function characterCard(', 'function renderWorldInspector('),context);
  assert.doesNotMatch(context.characterCard(character),/尚未签署/);
  character.has_messages=false;character.pulse_number=1;
  assert.doesNotMatch(context.characterCard(character),/尚未签署/);
  character.encounter_hook='在书房等候';
  assert.match(context.characterCard(character),/在书房等候/);
});

test('send is visible before network and completion replaces an unpainted partial reply', async () => {
  const state={conversation:{conversation:{id:'c'},needs_scene_entry:false},world:{world:{id:'w'}},liveTurns:new Map(),streamingConversationIds:new Set(),writingConversationIds:new Set(),conversationDrafts:new Map()};
  let release;
  const response=new Promise(resolve => {release=resolve;});
  const input={value:'你好'};
  const context=vm.createContext({state,TextDecoder,Uint8Array,
    $:selector=>selector==='#composer-input'?input:{scrollTop:0,scrollHeight:100},
    worldBusy:()=>false,pendingSettlements:()=>[],setActivity:()=>{},renderChat:()=>{},renderCurrent:()=>{},toast:()=>{},
    appFetch:()=>response,api:async()=>{throw Error('history unavailable');},refreshWorld:async()=>{},
    requestAnimationFrame:()=>1,cancelAnimationFrame:()=>{},
  });
  vm.runInContext(between('async function readStream(', 'function toast(')+between('async function sendTurn(', 'function isNearMessagesEnd('),context);
  const pending=context.sendTurn(false);
  assert.equal(state.liveTurns.get('c').messages.get('pending-user').content,'你好');
  assert.equal(state.liveTurns.get('c').messages.get('pending-assistant').status,'streaming');
  release(new Response([
    {type:'user',message:{id:'u',role:'user',content:'你好',status:'complete'}},
    {type:'assistant_start',message:{id:'a',role:'assistant',content:'',status:'streaming'}},
    {type:'delta',full:'尚未显示完'},
    {type:'assistant_complete',content:'这是已经完整生成的最终正文。'},
    {type:'done'},
  ].map(JSON.stringify).join('\n')+'\n'));
  await pending;
  const messages=state.liveTurns.get('c').messages;
  assert.deepEqual([...messages.keys()],['u','a']);
  assert.equal(messages.get('a').content,'这是已经完整生成的最终正文。');
  assert.equal(messages.get('a').status,'complete');
  assert.equal(state.streamingConversationIds.size,0);
});

test('unchanged and soft-only settlements have no change receipt',()=>{
  const context=vm.createContext({escapeHTML:x=>String(x)});
  vm.runInContext(between('function readableList(', 'function renderCharacterInspector('),context);
  assert.equal(context.recordHTML(null,{scene:{location:'金枝厅',physical_state:'靠回椅背'},relationship:{trust_delta:0},evidence:{}}),'');
  assert.match(context.settlementHTML({relationship:{trust_delta:1},evidence:{relationship:{}}}),/信任 \+1/);
});

test('scroll following is restored before another render can mistake it for reading history',()=>{
  const scroller={scrollTop:0,scrollHeight:900};
  const root={dataset:{},querySelectorAll:()=>[]};
  const state={viewMemory:new Map([['chat:c',{top:300,end:true,openDetails:[]}]])};
  const context=vm.createContext({state,$:selector=>selector==='.messages'?scroller:null,resizeComposer:()=>{},requestAnimationFrame:()=>1,rememberView:()=>{},updateLatestButton:()=>{}});
  vm.runInContext(between('function restoreView(', 'function updateLatestButton('),context);
  context.restoreView(root,'chat:c');assert.equal(scroller.scrollTop,900);
  state.viewMemory.set('chat:c',{top:120,end:false,openDetails:[]});
  context.restoreView(root,'chat:c');assert.equal(scroller.scrollTop,120);
});
