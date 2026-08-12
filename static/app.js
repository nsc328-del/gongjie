const state = {
  worlds: [], world: null, provider: null, view: "home",
  conversation: null, character: null, streaming: false,
  editingWorldId: null, editingCharacterId: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = (value = "") => String(value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
const clamp = (value, min = 0, max = 100) => Math.max(min, Math.min(max, Number(value) || 0));

async function api(path, options = {}) {
  const response = await fetch(path, {headers:{"Content-Type":"application/json", ...(options.headers || {})}, ...options});
  if (!response.ok) {
    let detail = "请求失败";
    try { const body = await response.json(); detail = typeof body.detail === "string" ? body.detail : (body.detail?.message || detail); } catch {}
    throw new Error(detail);
  }
  return response.json();
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 2300);
}

function avatar(character, size = "") {
  return `<span class="avatar ${size}" style="--accent:${escapeHTML(character.accent || "#bc5d45")}">${escapeHTML(character.name?.slice(0,1) || "·")}</span>`;
}

async function bootstrap() {
  const data = await api("/api/bootstrap");
  state.worlds = data.worlds;
  state.provider = data.provider;
  renderProviderState();
  if (!state.worlds.length) return;
  await loadWorld(state.worlds[0].id);
  fillProviderForm();
}

async function loadWorld(worldId, switchView = true) {
  state.world = await api(`/api/worlds/${worldId}`);
  state.character = null;
  state.conversation = null;
  renderShell();
  renderHome();
  renderTimeline();
  renderWorldInspector();
  if (switchView) showView("home");
}

function renderShell() {
  const {world, characters} = state.world;
  $("#world-name-side").textContent = world.name;
  $("#world-clock").textContent = world.current_time;
  $("#character-count").textContent = characters.length;
  const worldSelect = $("#world-select");
  worldSelect.innerHTML = state.worlds.map(item => `<option value="${item.id}" ${item.id === world.id ? "selected" : ""}>${escapeHTML(item.name)}</option>`).join("") + '<option value="__new__">＋ 新世界</option>';
  $("#character-list").innerHTML = characters.map(character => `
    <button class="character-button ${state.character?.id === character.id ? "active" : ""}" data-character="${character.id}">
      ${avatar(character)}
      <span><strong>${escapeHTML(character.name)}</strong><small>${escapeHTML(character.role)}</small></span>
      <i class="presence" style="--accent:${escapeHTML(character.accent)}"></i>
    </button>`).join("");
  $$('[data-character]').forEach(button => button.onclick = () => openCharacter(button.dataset.character));
  if (state.view !== "chat") updateHeaderForView();
}

function renderHome() {
  const {world, locations, characters, events} = state.world;
  $("#home-view").innerHTML = `<div class="home-wrap">
    <article class="world-hero">
      <span class="stage-eyebrow">${escapeHTML(world.atmosphere)}</span>
      <h2>${escapeHTML(world.name)}</h2>
      <p>${escapeHTML(world.premise)}</p>
    </article>
    <div class="section-head"><h3>地点</h3><span>${locations.length} 个持续存在的现场</span></div>
    <div class="location-grid">${locations.map((location, index) => `
      <article class="location-card"><small>0${index + 1} · ${escapeHTML(location.mood)}</small><h4>${escapeHTML(location.name)}</h4><p>${escapeHTML(location.description)}</p></article>`).join("")}</div>
    <div class="section-head"><h3>同一条时间线上的人</h3><span><button class="text-button" data-action="new-character">＋ 新人物</button> · ${events.length ? `${events.length} 件正史` : "正史尚未落笔"}</span></div>
    <div class="person-grid">${characters.map(character => `
      <article class="person-card" data-home-character="${character.id}"><div class="person-card-top">${avatar(character)}<div><h4>${escapeHTML(character.name)}</h4><small>${character.age} · ${escapeHTML(character.role)}</small></div></div><p>“${escapeHTML(character.tagline)}”</p></article>`).join("")}</div>
  </div>`;
  $$('[data-home-character]').forEach(card => card.onclick = () => openCharacter(card.dataset.homeCharacter));
}

function renderTimeline() {
  const events = state.world.events;
  $("#timeline-view").innerHTML = `<div class="timeline-wrap">
    <div class="timeline-intro"><span class="stage-eyebrow">CANON</span><h2>已经发生的事</h2><p>它们会被所有该知道的人带进下一次相遇。</p></div>
    <div class="event-list">${events.length ? events.map(event => `
      <article class="event-card"><h4>${escapeHTML(event.title)}</h4><p>${escapeHTML(event.details)}</p><div class="event-meta"><span>${escapeHTML(event.world_time || "时间未记")}</span><span>${escapeHTML(event.location_name || "地点未记")}</span><span>${event.visibility === "public" ? "公开" : event.visibility === "secret" ? "秘密" : "私下"}</span></div></article>`).join("") : '<div class="empty-state">第一件真正发生的事，会从一次对话开始。</div>'}</div>
  </div>`;
}

function renderWorldInspector() {
  const {world, events, characters, locations} = state.world;
  $("#inspector").innerHTML = `<div class="world-inspector">
    <div class="inspector-title">世界底稿</div><h2>${escapeHTML(world.name)}</h2><p>${escapeHTML(world.rules)}</p>
    <div class="inspector-actions"><button class="soft-button" data-action="edit-world">编辑世界</button><button class="soft-button" data-action="new-character">新人物</button></div>
    <article class="inspector-card"><h4>此刻</h4><p>${escapeHTML(world.current_time)}<br>${escapeHTML(world.atmosphere)}</p></article>
    <article class="inspector-card"><h4>脉搏</h4><div class="metric"><div class="metric-head"><span>人物</span><b>${characters.length}</b></div><div class="metric-track"><i style="width:${Math.min(100, characters.length * 24)}%"></i></div></div><div class="metric"><div class="metric-head"><span>地点</span><b>${locations.length}</b></div><div class="metric-track"><i style="width:${Math.min(100, locations.length * 24)}%"></i></div></div><div class="metric"><div class="metric-head"><span>正史</span><b>${events.length}</b></div><div class="metric-track"><i style="width:${Math.min(100, events.length * 12)}%"></i></div></div></article>
  </div>`;
}

async function openCharacter(characterId) {
  try {
    const data = await api("/api/conversations", {method:"POST", body:JSON.stringify({world_id:state.world.world.id, character_id:characterId})});
    state.conversation = data;
    state.character = data.character;
    renderShell();
    renderChat();
    renderCharacterInspector();
    showView("chat");
  } catch (error) { toast(error.message); }
}

async function reloadConversation() {
  if (!state.conversation) return;
  state.conversation = await api(`/api/conversations/${state.conversation.conversation.id}?branch_id=${state.conversation.branch_id}`);
  state.character = state.conversation.character;
  renderChat();
  renderCharacterInspector();
}

function renderChat() {
  const data = state.conversation;
  const character = data.character;
  const canonical = data.conversation.canonical_branch_id === data.branch_id;
  $("#chat-view").innerHTML = `
    <div class="chat-toolbar">
      <div class="chat-person">${avatar(character)}<div><strong>${escapeHTML(character.name)}</strong><small>${escapeHTML(character.location_name || character.role)}</small></div></div>
      <select id="branch-select" class="branch-select">${data.branches.map(branch => `<option value="${branch.id}" ${branch.id === data.branch_id ? "selected" : ""}>${escapeHTML(branch.title)}</option>`).join("")}</select>
      ${canonical ? '<span class="canon-badge">正史</span>' : '<button id="make-canon" class="soft-button">写入正史</button>'}
    </div>
    <div id="messages" class="messages">${data.messages.length ? data.messages.map(messageHTML).join("") : `<div class="empty-state">${escapeHTML(character.tagline)}<br><br>在这里说第一句话。</div>`}</div>
    <div class="composer-shell"><div class="composer">
      <textarea id="composer-input" placeholder="对 ${escapeHTML(character.name)} 说……" rows="2"></textarea>
      <div class="composer-actions"><button id="continue-turn" class="continue-button" ${data.messages.length ? "" : "disabled"}>继续这一刻</button><button id="send-turn" class="send-button">↑</button></div>
    </div></div>`;
  $("#branch-select").onchange = event => switchBranch(event.target.value);
  if ($("#make-canon")) $("#make-canon").onclick = makeCanon;
  $("#send-turn").onclick = () => sendTurn(false);
  $("#continue-turn").onclick = () => sendTurn(true);
  $("#composer-input").onkeydown = event => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendTurn(false); }
  };
  $$('[data-fork-message]').forEach(button => button.onclick = () => forkFrom(button.dataset.forkMessage));
  requestAnimationFrame(scrollMessages);
}

function messageHTML(message) {
  const who = message.role === "user" ? "你" : state.character.name;
  const fork = message.role === "assistant" && message.status !== "streaming" ? `<div class="message-actions"><button class="text-button" data-fork-message="${message.id}">从这里分岔</button></div>` : "";
  return `<article class="message ${message.role} ${message.status === "error" ? "error" : ""}" data-message-id="${message.id}"><div class="message-label">${escapeHTML(who)}</div><div class="message-body ${message.status === "streaming" ? "typing-caret" : ""}">${escapeHTML(message.content)}</div>${fork}</article>`;
}

function renderCharacterInspector() {
  const data = state.conversation;
  const character = data.character;
  const relationship = state.world.relationships.find(item => item.from_character_id === character.id && item.to_user) || {};
  const desires = Object.entries(character.desire_profile || {});
  const threads = data.scene.active_threads || [];
  const sceneLocation = state.world.locations.find(item => item.id === data.scene.location_id)?.name || character.location_name || "地点未定";
  $("#inspector").innerHTML = `
    <div class="inspector-title">当前人物</div>
    <div class="inspector-profile">${avatar(character)}<h2>${escapeHTML(character.name)}</h2><p>${escapeHTML(character.role)} · ${character.age}</p></div>
    <div class="inspector-actions"><button class="soft-button" data-action="edit-character">编辑人物</button></div>
    <article class="inspector-card"><h4>现场</h4><p>${escapeHTML(sceneLocation)}<br>${escapeHTML(data.scene.physical_state || "距离尚未改变")}</p><div class="metric"><div class="metric-head"><span>张力</span><b>${clamp(data.scene.tension)}</b></div><div class="metric-track"><i style="width:${clamp(data.scene.tension)}%"></i></div></div></article>
    <article class="inspector-card"><h4>与你</h4><p>${escapeHTML(relationship.label || "尚未命名")}</p>${metric("信任",relationship.trust)}${metric("欲望",relationship.desire)}${metric("拉扯",relationship.tension)}</article>
    <article class="inspector-card"><h4>欲望指纹</h4>${desires.map(([name,value]) => metric(desireName(name),value)).join("")}</article>
    <article class="inspector-card"><h4>还在发生</h4><ul class="thread-list">${threads.length ? threads.map(item => `<li>${escapeHTML(item)}</li>`).join("") : "<li>尚未形成牵引</li>"}</ul></article>
    <article class="inspector-card"><h4>${escapeHTML(character.name)}知道的事</h4><ul class="knowledge-list">${data.knowledge.length ? data.knowledge.slice(0,8).map(item => `<li>${escapeHTML(item.belief || item.title)}</li>`).join("") : "<li>还没有只属于此人的事实</li>"}</ul></article>`;
}

function metric(name, value) {
  const normalized = clamp(value);
  return `<div class="metric"><div class="metric-head"><span>${escapeHTML(name)}</span><b>${normalized}</b></div><div class="metric-track"><i style="width:${normalized}%"></i></div></div>`;
}
function desireName(key) { return ({control:"掌控", devotion:"投入", jealousy:"嫉妒", risk:"冒险", tenderness:"温柔"})[key] || key; }

async function sendTurn(continuation) {
  if (state.streaming) return;
  const input = $("#composer-input");
  const content = continuation ? "" : input.value.trim();
  if (!continuation && !content) return;
  state.streaming = true;
  input.value = "";
  setComposerDisabled(true);
  const messages = $("#messages");
  if ($(".empty-state", messages)) messages.innerHTML = "";
  let assistantNode = null;
  try {
    const response = await fetch(`/api/conversations/${state.conversation.conversation.id}/turn`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({content, branch_id:state.conversation.branch_id, continuation}),
    });
    if (!response.ok || !response.body) throw new Error("没有接到世界的回应");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const {value, done} = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), {stream:!done});
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.type === "user") messages.insertAdjacentHTML("beforeend", messageHTML(event.message));
        if (event.type === "assistant_start") {
          messages.insertAdjacentHTML("beforeend", messageHTML(event.message));
          assistantNode = $(`[data-message-id="${event.message.id}"]`, messages);
        }
        if (event.type === "delta" && assistantNode) {
          $(".message-body", assistantNode).textContent = event.full;
          scrollMessages();
        }
        if (event.type === "error") {
          if (assistantNode) { assistantNode.classList.add("error"); $(".message-body", assistantNode).classList.remove("typing-caret"); if (!event.partial) $(".message-body", assistantNode).textContent = event.error.message; }
          throw new Error(event.error.message);
        }
        if (event.type === "patch_skipped") toast(event.message);
      }
      if (done) break;
    }
    await Promise.all([reloadConversation(), refreshWorldSilently()]);
  } catch (error) {
    toast(error.message);
    await reloadConversation().catch(() => {});
  } finally {
    state.streaming = false;
    setComposerDisabled(false);
    $("#composer-input")?.focus();
  }
}

function setComposerDisabled(disabled) {
  [$("#send-turn"), $("#continue-turn"), $("#composer-input"), $("#branch-select")].filter(Boolean).forEach(node => node.disabled = disabled);
}
function scrollMessages() { const node = $("#messages"); if (node) node.scrollTop = node.scrollHeight; }

async function refreshWorldSilently() {
  const characterId = state.character?.id;
  state.world = await api(`/api/worlds/${state.world.world.id}`);
  if (characterId) state.character = state.world.characters.find(item => item.id === characterId) || state.character;
  renderShell(); renderHome(); renderTimeline();
}

async function switchBranch(branchId) {
  if (state.streaming) return;
  state.conversation = await api(`/api/conversations/${state.conversation.conversation.id}/branches/${branchId}/activate`, {method:"POST", body:"{}"});
  renderChat(); renderCharacterInspector();
}

async function forkFrom(messageId) {
  if (state.streaming) return;
  try {
    state.conversation = await api(`/api/conversations/${state.conversation.conversation.id}/branches`, {
      method:"POST", body:JSON.stringify({parent_branch_id:state.conversation.branch_id, from_message_id:messageId}),
    });
    renderChat(); renderCharacterInspector(); toast("新支线已经展开");
  } catch (error) { toast(error.message); }
}

async function makeCanon() {
  if (state.streaming) return;
  try {
    state.conversation = await api(`/api/conversations/${state.conversation.conversation.id}/branches/${state.conversation.branch_id}/canon`, {method:"POST", body:"{}"});
    await refreshWorldSilently(); renderChat(); renderCharacterInspector(); toast("这条支线已成为正史");
  } catch (error) { toast(error.message); }
}

function showView(view) {
  state.view = view;
  $$(".view").forEach(node => node.classList.toggle("active", node.id === `${view}-view`));
  $$(".nav-button").forEach(node => node.classList.toggle("active", node.dataset.view === view));
  if (view !== "chat") { state.character = null; renderShell(); renderWorldInspector(); }
  updateHeaderForView();
}

function updateHeaderForView() {
  if (!state.world) return;
  const title = state.view === "timeline" ? "正史" : state.view === "chat" && state.character ? state.character.name : state.world.world.name;
  $("#stage-title").textContent = title;
  $("#stage-eyebrow").textContent = state.view === "chat" ? "私人现场 · 共享后果" : state.view === "timeline" ? "共享时间线" : "共享世界";
}

function renderProviderState() {
  const node = $("#provider-state");
  const connected = Boolean(state.provider?.has_key && state.provider?.model);
  node.classList.toggle("online", connected);
  $("span", node).textContent = connected ? state.provider.model : "未连接";
  $$("[data-action=settings]").forEach(button => { if (button.classList.contains("soft-button")) button.textContent = connected ? state.provider.model : "连接模型"; });
}

function fillProviderForm() {
  const form = $("#provider-form");
  const profile = state.provider || {protocol:"openai-compatible",base_url:"https://api.openai.com",path:"/v1/chat/completions",model:"",max_tokens:16384,temperature:.95};
  for (const key of ["protocol","base_url","path","model","max_tokens","temperature"]) if (form.elements[key]) form.elements[key].value = profile[key] ?? "";
  form.elements.api_key.value = "";
}

function openSettings() { fillProviderForm(); $("#provider-feedback").textContent = ""; $("#provider-dialog").showModal(); }
function closeSettings() { $("#provider-dialog").close(); }

function openWorldEditor(isNew = false) {
  state.editingWorldId = isNew ? null : state.world.world.id;
  const form = $("#world-form");
  const world = isNew ? {name:"",premise:"",atmosphere:"",rules:"所有角色均为成年人；角色拥有自主意志；意图不是事实。",current_time:""} : state.world.world;
  $("#world-dialog-title").textContent = isNew ? "新世界" : "世界底稿";
  for (const key of ["name","premise","atmosphere","rules","current_time"]) form.elements[key].value = world[key] || "";
  $("#location-editor").style.display = isNew ? "none" : "block";
  $("#editor-location-list").innerHTML = isNew ? "" : state.world.locations.map(item => `<span>${escapeHTML(item.name)}</span>`).join("");
  $("#new-location-name").value = ""; $("#new-location-mood").value = ""; $("#new-location-description").value = "";
  $("#world-dialog").showModal();
}

function closeWorldEditor() { $("#world-dialog").close(); if (state.world) $("#world-select").value = state.world.world.id; }

function openCharacterEditor(isNew = false) {
  state.editingCharacterId = isNew ? null : state.character?.id;
  const form = $("#character-form");
  const character = isNew ? {name:"",age:25,pronouns:"",role:"",tagline:"",persona:"",speech_style:"",signature_behaviors:[],loss_of_control:"",desire_profile:{control:50,devotion:50,jealousy:50,risk:50,tenderness:50},accent:"#bc5d45",current_location_id:state.world.locations[0]?.id || ""} : state.world.characters.find(item => item.id === state.editingCharacterId);
  if (!character && !isNew) return;
  $("#character-dialog-title").textContent = isNew ? "新人物" : "人物底色";
  for (const key of ["name","age","pronouns","role","tagline","persona","speech_style","loss_of_control","accent"]) form.elements[key].value = character[key] ?? "";
  form.elements.signature_behaviors.value = (character.signature_behaviors || []).join("\n");
  form.elements.current_location_id.innerHTML = '<option value="">未定</option>' + state.world.locations.map(item => `<option value="${item.id}">${escapeHTML(item.name)}</option>`).join("");
  form.elements.current_location_id.value = character.current_location_id || "";
  for (const key of ["control","devotion","jealousy","risk","tenderness"]) form.elements[key].value = character.desire_profile?.[key] ?? 50;
  $("#character-dialog").showModal();
}

function closeCharacterEditor() { $("#character-dialog").close(); }

$("#provider-protocol").onchange = event => {
  const form = $("#provider-form");
  if (event.target.value === "anthropic-messages") { form.elements.base_url.value = "https://api.anthropic.com"; form.elements.path.value = "/v1/messages"; }
  else { form.elements.base_url.value = "https://api.openai.com"; form.elements.path.value = "/v1/chat/completions"; }
};

$("#provider-form").onsubmit = async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const body = Object.fromEntries(new FormData(form));
  body.max_tokens = Number(body.max_tokens); body.temperature = Number(body.temperature); body.id = state.provider?.id || "provider_default";
  try {
    state.provider = await api("/api/provider", {method:"PUT", body:JSON.stringify(body)});
    renderProviderState();
    $("#provider-feedback").className = "form-feedback good";
    $("#provider-feedback").textContent = "已保存";
    setTimeout(closeSettings, 500);
  } catch (error) { $("#provider-feedback").className = "form-feedback bad"; $("#provider-feedback").textContent = error.message; }
};

$("#test-provider").onclick = async () => {
  const feedback = $("#provider-feedback");
  feedback.className = "form-feedback"; feedback.textContent = "正在连接…";
  try {
    const form = $("#provider-form");
    const body = Object.fromEntries(new FormData(form)); body.max_tokens = Number(body.max_tokens); body.temperature = Number(body.temperature); body.id = state.provider?.id || "provider_default";
    state.provider = await api("/api/provider", {method:"PUT", body:JSON.stringify(body)});
    const result = await api("/api/provider/test", {method:"POST", body:"{}"});
    feedback.className = "form-feedback good"; feedback.textContent = `${result.model} · ${result.reply || "连接成功"}`; renderProviderState();
  } catch (error) { feedback.className = "form-feedback bad"; feedback.textContent = error.message; }
};

document.addEventListener("click", event => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "settings") openSettings();
  if (action === "close-settings") closeSettings();
  if (action === "home") showView("home");
  if (action === "edit-world") openWorldEditor(false);
  if (action === "new-world") openWorldEditor(true);
  if (action === "close-world-editor") closeWorldEditor();
  if (action === "new-character") openCharacterEditor(true);
  if (action === "edit-character") openCharacterEditor(false);
  if (action === "close-character-editor") closeCharacterEditor();
  const view = event.target.closest("[data-view]")?.dataset.view;
  if (view) showView(view);
});

$("#world-select").onchange = event => {
  if (event.target.value === "__new__") openWorldEditor(true);
  else loadWorld(event.target.value);
};

$("#world-form").onsubmit = async event => {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(event.currentTarget));
  try {
    const bundle = state.editingWorldId
      ? await api(`/api/worlds/${state.editingWorldId}`, {method:"PUT", body:JSON.stringify(body)})
      : await api("/api/worlds", {method:"POST", body:JSON.stringify(body)});
    if (!state.editingWorldId) state.worlds.push(bundle.world);
    closeWorldEditor();
    await loadWorld(bundle.world.id);
    toast("世界已保存");
  } catch (error) { toast(error.message); }
};

$("#add-location").onclick = async () => {
  const name = $("#new-location-name").value.trim();
  if (!name || !state.editingWorldId) return;
  try {
    await api(`/api/worlds/${state.editingWorldId}/locations`, {method:"POST", body:JSON.stringify({name,mood:$("#new-location-mood").value.trim(),description:$("#new-location-description").value.trim()})});
    state.world = await api(`/api/worlds/${state.editingWorldId}`);
    $("#editor-location-list").innerHTML = state.world.locations.map(item => `<span>${escapeHTML(item.name)}</span>`).join("");
    $("#new-location-name").value = ""; $("#new-location-mood").value = ""; $("#new-location-description").value = "";
    renderShell(); renderHome(); toast("地点已加入");
  } catch (error) { toast(error.message); }
};

$("#character-form").onsubmit = async event => {
  event.preventDefault();
  const raw = Object.fromEntries(new FormData(event.currentTarget));
  const body = {
    name:raw.name, age:Number(raw.age), pronouns:raw.pronouns, role:raw.role, tagline:raw.tagline,
    persona:raw.persona, speech_style:raw.speech_style, loss_of_control:raw.loss_of_control,
    current_location_id:raw.current_location_id || null, accent:raw.accent,
    signature_behaviors:raw.signature_behaviors.split("\n").map(item => item.trim()).filter(Boolean),
    desire_profile:Object.fromEntries(["control","devotion","jealousy","risk","tenderness"].map(key => [key,clamp(raw[key])])),
  };
  try {
    const character = state.editingCharacterId
      ? await api(`/api/characters/${state.editingCharacterId}`, {method:"PUT", body:JSON.stringify(body)})
      : await api(`/api/worlds/${state.world.world.id}/characters`, {method:"POST", body:JSON.stringify(body)});
    closeCharacterEditor();
    await refreshWorldSilently();
    if (state.editingCharacterId) await openCharacter(character.id); else { showView("home"); toast("人物已加入这个世界"); }
  } catch (error) { toast(error.message); }
};

bootstrap().catch(error => toast(error.message));
