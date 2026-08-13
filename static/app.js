const state = {
  worlds: [], world: null, provider: null,
  screen: "library", view: "world",
  conversation: null, character: null,
  rooms: [], room: null, roomStreaming: false,
  streaming: false, syncing: false, openingConversation: false,
  playingWorldIntro: false,
  editingWorldId: null, editingCharacterId: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = (value = "") => String(value).replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
const clamp = (value, min = 0, max = 100) => Math.max(min, Math.min(max, Number(value) || 0));

async function api(path, options = {}) {
  const response = await fetch(path, {headers:{"Content-Type":"application/json", ...(options.headers || {})}, ...options});
  if (!response.ok) {
    let detail = "请求失败";
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : (body.detail?.message || detail);
    } catch {}
    throw new Error(detail);
  }
  return response.json();
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 2600);
}

function avatar(character, size = "") {
  const accent = /^#[0-9a-f]{6}$/i.test(character.accent || "") ? character.accent : "#bc5d45";
  return `<span class="avatar ${size}" style="--accent:${accent}">${escapeHTML(character.name?.slice(0,1) || "·")}</span>`;
}

function relationshipFor(characterId) {
  return state.world?.relationships.find(item => item.from_character_id === characterId && item.to_user) || {};
}

async function bootstrap() {
  const data = await api("/api/bootstrap");
  state.worlds = data.worlds;
  state.provider = data.provider;
  renderProviderState();
  renderLibrary();
  showLibrary(false);
  fillProviderForm();
}

async function refreshBootstrap() {
  const data = await api("/api/bootstrap");
  state.worlds = data.worlds;
  state.provider = data.provider;
  renderProviderState();
  renderLibrary();
}

function renderLibrary() {
  const grid = $("#world-library-grid");
  const cards = state.worlds.map(world => `
    <button class="library-world-card" data-open-world="${world.id}">
      <span class="world-card-kicker">${escapeHTML(world.atmosphere || "尚待落笔")}</span>
      <strong>${escapeHTML(world.name)}</strong>
      <p>${escapeHTML(world.premise || "这个世界还没有写下前提。")}</p>
      <div class="world-card-meta">
        <span>${world.character_count || 0} 人</span>
        <span>${escapeHTML(world.current_time || "时间未定")}</span>
      </div>
      <div class="world-card-last">${world.latest_public_event ? `最近：${escapeHTML(world.latest_public_event)}` : "还没有公开的大事发生"}</div>
      ${world.pending_sync_count ? `<em>${world.pending_sync_count} 件变化待同步</em>` : ""}
    </button>`).join("");
  grid.innerHTML = cards + `
    <button class="new-world-card" data-action="new-world">
      <span>＋</span><strong>开启新世界</strong><small>空白世界书或预制序章</small>
    </button>`;
  $$('[data-open-world]').forEach(card => card.onclick = () => openWorld(card.dataset.openWorld));
}

function showLibrary(refresh = true) {
  state.screen = "library";
  $("#library-screen").classList.remove("hidden");
  $("#world-shell").classList.add("hidden");
  if (refresh) refreshBootstrap().catch(error => toast(error.message));
}

async function openWorld(worldId) {
  try {
    state.world = await api(`/api/worlds/${worldId}`);
    state.rooms = (await api(`/api/worlds/${worldId}/rooms`)).rooms || [];
    state.conversation = null;
    state.character = null;
    state.room = null;
    state.screen = "world";
    state.view = "world";
    state.playingWorldIntro = true;
    clearTimeout(openWorld.introTimer);
    $("#library-screen").classList.add("hidden");
    $("#world-shell").classList.remove("hidden");
    renderShell();
    renderWorld();
    renderWorldInspector();
    showView("world");
    openWorld.introTimer = setTimeout(() => {
      if (state.world?.world.id !== worldId) return;
      state.playingWorldIntro = false;
      $("#world-view .world-wrap")?.classList.remove("entering");
    }, 2200);
  } catch (error) { toast(error.message); }
}

async function refreshWorld() {
  if (!state.world) return;
  state.world = await api(`/api/worlds/${state.world.world.id}`);
  state.rooms = (await api(`/api/worlds/${state.world.world.id}/rooms`)).rooms || [];
  if (state.character) {
    state.character = state.world.characters.find(item => item.id === state.character.id) || state.character;
  }
  renderShell();
  renderWorld();
}

function renderShell() {
  const {world, characters} = state.world;
  $("#world-name-side").textContent = world.name;
  $("#world-clock").textContent = world.current_time || "时间未定";
  $("#character-count").textContent = characters.length;
  $("#character-list").innerHTML = characters.map(character => `
    <button class="character-button ${state.character?.id === character.id ? "active" : ""}" data-character="${character.id}">
      ${avatar(character)}
      <span><strong>${escapeHTML(character.name)}</strong><small>${escapeHTML(character.role)}</small></span>
      <i class="presence" style="--accent:${/^#[0-9a-f]{6}$/i.test(character.accent || "") ? character.accent : "#bc5d45"}"></i>
    </button>`).join("");
  $$('[data-character]').forEach(button => button.onclick = () => openCharacter(button.dataset.character));
  updateHeader();
}

function renderWorld() {
  const {world, locations, characters, events, pending_sync_count: pending} = state.world;
  $("#world-view").innerHTML = `<div class="world-wrap ${state.playingWorldIntro ? "entering" : ""}">
    <article class="world-hero">
      <div><span class="stage-eyebrow">此刻发生</span><h2>${escapeHTML(world.current_time || world.name)}</h2><p>${escapeHTML(world.opening_narration || world.premise || "这一刻还没有落笔。")}</p></div>
      <div class="world-hero-time"><small>${escapeHTML(world.atmosphere || "共享世界")}</small><strong>${escapeHTML(world.name)}</strong></div>
    </article>

    <div class="section-head character-reveal-head"><div><span class="stage-eyebrow">此刻可以去见</span><h3>这个世界里的人</h3></div><button class="text-button" data-action="new-character">＋ 添加人物</button></div>
    <div class="world-character-grid">${characters.length ? characters.map((character, index) => characterCard(character, index)).join("") : `<button class="empty-character-card" data-action="new-character">写下这个世界的第一个人物</button>`}</div>

    <div class="section-head character-reveal-head"><div><span class="stage-eyebrow">SAME SCENE</span><h3>同场房间</h3></div><button class="text-button" data-action="new-room">＋ 开群</button></div>
    <div class="room-list">${state.rooms.length ? state.rooms.map(roomCard).join("") : `<div class="room-enter">拉几个人到同一个场景里，修罗场当场开演。</div>`}</div>

    <section class="sync-panel world-secondary ${pending ? "waiting" : "quiet"}">
      <div class="sync-orbit"><i></i><i></i><i></i></div>
      <div><span class="stage-eyebrow">WORLD PULSE</span><h3>${pending ? `${pending} 件新变化尚未传开` : "所有人都跟上了此刻"}</h3><p>${pending ? "让公开发生的事抵达每个该知道的人。" : "新的事实出现后，可以在这里推动世界回响。"}</p></div>
      <button id="sync-world" class="primary-button" ${!pending || state.syncing ? "disabled" : ""}>${state.syncing ? "正在同步…" : "同步角色认知"}</button>
    </section>

    <div class="world-columns world-secondary">
      <section>
        <div class="section-head compact"><div><span class="stage-eyebrow">PUBLIC RECORD</span><h3>世界大事</h3></div></div>
        <div class="event-list">${events.length ? events.slice(0,8).map(event => `
          <article class="event-card"><span></span><div><h4>${escapeHTML(event.title)}</h4><p>${escapeHTML(event.details)}</p><small>${escapeHTML(event.world_time || "时间未记")} · ${escapeHTML(event.location_name || "地点未记")}</small></div></article>`).join("") : `<div class="empty-state">第一件公开的大事，还没有发生。</div>`}</div>
      </section>
      <section>
        <div class="section-head compact"><div><span class="stage-eyebrow">PLACES</span><h3>仍在呼吸的地方</h3></div></div>
        <div class="place-list">${locations.length ? locations.map(location => `<article><small>${escapeHTML(location.mood)}</small><h4>${escapeHTML(location.name)}</h4><p>${escapeHTML(location.description)}</p></article>`).join("") : `<div class="empty-state">这个世界还没有地点。</div>`}</div>
      </section>
    </div>
  </div>`;
  $$('[data-world-character]').forEach(card => card.onclick = () => openCharacter(card.dataset.worldCharacter));
  $$('[data-room]').forEach(card => card.onclick = () => openRoom(card.dataset.room));
  $("#sync-world")?.addEventListener("click", syncWorld);
}

function roomCard(room) {
  const names = (room.member_names || "").split("、").filter(Boolean);
  const members = state.world.characters
    .filter(character => names.includes(character.name))
    .map(character => avatar(character));
  return `<button class="room-card" data-room="${room.id}">
    <div class="room-card-head"><strong>${escapeHTML(room.name)}</strong><span>${escapeHTML(room.location_name || "地点未定")}</span></div>
    <div class="room-members">${members.length ? members.join("") : ""}<small>${room.member_count || 0} 人 · ${escapeHTML(room.last_content ? "刚有过动静" : "还没开场")}</small></div>
    ${room.last_content ? `<div class="room-card-last">${escapeHTML(room.last_content)}</div>` : ""}
  </button>`;
}

function openRoomDialog() {
  const form = $("#room-form");
  form.elements.name.value = "";
  const locationSelect = form.elements.location_id;
  locationSelect.innerHTML = '<option value="">未定</option>' + state.world.locations.map(item => `<option value="${item.id}">${escapeHTML(item.name)}</option>`).join("");
  const picker = $("#room-character-picker");
  picker.innerHTML = state.world.characters.map(character => `
    <label><input type="checkbox" value="${character.id}"><span>${avatar(character)}</span>${escapeHTML(character.name)}</label>`).join("");
  $("#room-dialog").showModal();
}

function closeRoomDialog() { $("#room-dialog").close(); }

async function openRoom(roomId) {
  try {
    state.room = await api(`/api/rooms/${roomId}`);
    state.conversation = null;
    state.character = null;
    state.view = "room";
    renderShell();
    renderRoom();
    renderRoomInspector();
    showView("room");
  } catch (error) { toast(error.message); }
}

function renderRoom() {
  const data = state.room;
  const room = data.room;
  const locked = state.roomStreaming;
  $("#room-view").innerHTML = `
    <div class="room-toolbar">
      <button class="chat-back" data-action="world-home">‹ 世界</button>
      <div class="chat-person"><div><strong>${escapeHTML(room.name)}</strong><small>${escapeHTML(data.location?.name || "地点未定")}</small></div></div>
      <div class="room-members">${data.characters.map(character => avatar(character)).join("")}</div>
      <span class="live-world-badge">同场 · 各说各话</span>
    </div>
    <div id="room-messages" class="messages">${data.messages.length ? data.messages.map(message => roomMessageHTML(message)).join("") : `<div class="empty-state opening"><p>${escapeHTML(room.name)}还没有开场。说一句话，让在场的人各自决定要不要接。</p></div>`}</div>
    <div class="composer-shell"><div class="composer">
      <textarea id="room-composer-input" placeholder="对房间里所有人说……（有人会回，有人会沉默）" rows="2" ${locked ? "disabled" : ""}></textarea>
      <div class="composer-actions"><button id="room-send-turn" class="send-button" aria-label="发送" ${locked ? "disabled" : ""}>↑</button></div>
    </div></div>`;
  $("#room-send-turn").onclick = () => sendRoomTurn();
  $("#room-composer-input").onkeydown = event => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendRoomTurn(); }
  };
  requestAnimationFrame(() => scrollRoomMessages());
}

function roomMessageHTML(message) {
  const who = message.role === "user" ? "你" : (message.character_name || "在场的人");
  const accent = /^#[0-9a-f]{6}$/i.test(message.character_accent || "") ? message.character_accent : "#bc5d45";
  const label = message.role === "user"
    ? `<div class="message-label">你</div>`
    : `<div class="message-label"><span class="avatar" style="--accent:${accent}">${escapeHTML((message.character_name || "·").slice(0, 1))}</span> ${escapeHTML(who)}</div>`;
  return `<article class="message ${message.role}" data-room-message-id="${message.id}">${label}<div class="message-body">${escapeHTML(message.content)}</div></article>`;
}

function renderRoomInspector() {
  const data = state.room;
  const room = data.room;
  const location = data.location;
  $("#inspector").innerHTML = `
    <div class="inspector-title">同场房间</div>
    <h2>${escapeHTML(room.name)}</h2>
    <p>${escapeHTML(location ? `${location.name} · ${location.mood}` : "地点未定")}</p>
    <div class="inspector-actions"><button class="soft-button" data-action="close-room">离开房间</button></div>
    <article class="inspector-card"><h4>在场的人</h4><ul class="thread-list">${data.characters.map(character => `<li>${escapeHTML(character.name)} · ${escapeHTML(character.role)}</li>`).join("") || "<li>还没有人</li>"}</ul></article>
    <article class="inspector-card"><h4>规则</h4><p>你发言后，每个角色独立决定是否回应，可以沉默；他们只对你说，不会互相搭话循环。</p></article>`;
}

async function sendRoomTurn() {
  if (state.roomStreaming) return;
  const input = $("#room-composer-input");
  const content = input.value.trim();
  if (!content) return;
  state.roomStreaming = true;
  input.value = "";
  input.disabled = true;
  $("#room-send-turn").disabled = true;
  const messages = $("#room-messages");
  if ($(".empty-state", messages)) messages.innerHTML = "";
  const silentLines = [];
  try {
    const response = await fetch(`/api/rooms/${state.room.room.id}/turn`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, continuation: false }),
    });
    if (!response.ok || !response.body) throw new Error("没有接到房间的回应");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.type === "user") messages.insertAdjacentHTML("beforeend", roomMessageHTML(event.message));
        if (event.type === "room_reply") {
          messages.insertAdjacentHTML("beforeend", roomMessageHTML(event.message));
          scrollRoomMessages();
        }
        if (event.type === "room_silent") {
          silentLines.push(event.character_name);
        }
        if (event.type === "world_patch" && event.result?.record) {
          messages.insertAdjacentHTML("beforeend", recordHTML(event.result.record));
          scrollRoomMessages();
        }
        if (event.type === "error") throw new Error(event.error.message);
      }
      if (done) break;
    }
    if (silentLines.length) {
      messages.insertAdjacentHTML("beforeend", `<div class="room-silent-note">${silentLines.map(escapeHTML).join("、")} 没有接话。</div>`);
      scrollRoomMessages();
    }
    await refreshWorld();
    state.room = await api(`/api/rooms/${state.room.room.id}`);
    renderRoom();
    renderRoomInspector();
  } catch (error) {
    toast(error.message);
    state.room = await api(`/api/rooms/${state.room.room.id}`).catch(() => state.room);
    renderRoom();
  } finally {
    state.roomStreaming = false;
    input.disabled = false;
    $("#room-send-turn").disabled = false;
    $("#room-composer-input")?.focus();
  }
}

function scrollRoomMessages() { const node = $("#room-messages"); if (node) node.scrollTop = node.scrollHeight; }

function characterCard(character, index = 0) {
  const relationship = relationshipFor(character.id);
  const summary = character.has_messages
    ? (character.state_summary || character.tagline || "还没有留下此刻的姿态。")
    : (character.opening_hook || character.tagline || "此刻可以去见他。");
  return `<button class="world-character-card" style="--card-index:${index}" data-world-character="${character.id}">
    <div class="character-card-head">${avatar(character, "large")}<div><h4>${escapeHTML(character.name)}</h4><small>${escapeHTML(character.role)} · ${escapeHTML(character.location_name || "地点未定")}</small></div><span>进入 ›</span></div>
    <p>${escapeHTML(summary)}</p>
    <div class="relationship-line"><span>${escapeHTML(relationship.label || "关系尚未命名")}</span><i></i><strong>${escapeHTML(relationship.notes || "还没有留下共同经历")}</strong></div>
  </button>`;
}

function renderWorldInspector() {
  const {world, characters, locations, events, pending_sync_count} = state.world;
  $("#inspector").innerHTML = `<div class="world-inspector">
    <div class="inspector-title">世界底稿</div>
    <h2>${escapeHTML(world.name)}</h2><p>${escapeHTML(world.rules)}</p>
    <div class="inspector-actions"><button class="soft-button" data-action="edit-world">编辑世界书</button><button class="soft-button" data-action="new-character">新人物</button></div>
    <article class="inspector-card"><h4>世界脉搏</h4>${metric("人物", characters.length, Math.max(3, characters.length))}${metric("地点", locations.length, Math.max(3, locations.length))}${metric("公开大事", events.length, Math.max(8, events.length))}</article>
    <article class="inspector-card"><h4>等待传播</h4><p>${pending_sync_count ? `${pending_sync_count} 件新变化还没有抵达所有该知道的人。` : "此刻没有滞留的世界回响。"}</p></article>
  </div>`;
}

async function syncWorld() {
  if (state.syncing || !state.world.pending_sync_count) return;
  state.syncing = true;
  renderWorld();
  try {
    const result = await api(`/api/worlds/${state.world.world.id}/sync`, {method:"POST", body:"{}"});
    await refreshWorld();
    renderWorldInspector();
    toast(result.synced_events ? `${result.synced_events} 件变化已经抵达角色` : "没有新的变化需要同步");
  } catch (error) {
    toast(error.message);
    if (/连接|auth|模型/i.test(error.message)) openSettings();
  } finally {
    state.syncing = false;
    renderWorld();
  }
}

async function openCharacter(characterId) {
  try {
    const data = await api("/api/conversations", {method:"POST", body:JSON.stringify({world_id:state.world.world.id, character_id:characterId})});
    state.conversation = data;
    state.character = data.character;
    state.view = "chat";
    state.openingConversation = !data.messages.length;
    renderShell();
    renderChat();
    renderCharacterInspector();
    showView("chat");
    if (!data.messages.length) generateOpening(data.conversation.id);
  } catch (error) { toast(error.message); }
}

async function generateOpening(conversationId = state.conversation?.conversation.id) {
  if (!conversationId) return;
  state.openingConversation = true;
  if (state.conversation?.conversation.id === conversationId) renderChat();
  try {
    await api(`/api/conversations/${conversationId}/opening`, {method:"POST", body:"{}"});
    if (state.conversation?.conversation.id === conversationId) {
      await refreshWorld();
      await reloadConversation();
    }
  } catch (error) {
    if (state.conversation?.conversation.id === conversationId) {
      toast(error.message);
      if (/连接|auth|模型/i.test(error.message)) openSettings();
    }
  } finally {
    if (state.conversation?.conversation.id === conversationId) {
      state.openingConversation = false;
      renderChat();
    }
  }
}

async function reloadConversation() {
  if (!state.conversation) return;
  state.conversation = await api(`/api/conversations/${state.conversation.conversation.id}`);
  state.character = state.conversation.character;
  renderChat();
  renderCharacterInspector();
}

function renderChat() {
  const data = state.conversation;
  const character = data.character;
  const recordsByMessage = Object.fromEntries(data.records.map(record => [record.source_message_id, record]));
  const opening = state.openingConversation
    ? `<div class="opening-sequence"><div class="opening-pulse"><i></i><i></i><i></i></div><p>${escapeHTML(character.name)}正在走进这一刻</p></div>`
    : `<div class="empty-state opening"><p>${escapeHTML(character.tagline)}</p><button id="retry-opening" class="soft-button">让${escapeHTML(character.name)}开口</button></div>`;
  const locked = state.streaming || state.openingConversation;
  $("#chat-view").innerHTML = `
    <div class="chat-toolbar">
      <button class="chat-back" data-action="world-home">‹ 世界</button>
      <div class="chat-person">${avatar(character)}<div><strong>${escapeHTML(character.name)}</strong><small>${escapeHTML(character.location_name || character.role)}</small></div></div>
      <span class="live-world-badge">世界持续记录</span>
    </div>
    <div id="messages" class="messages">${data.messages.length ? data.messages.map(message => messageHTML(message, recordsByMessage[message.id])).join("") : opening}</div>
    <div class="composer-shell"><div class="composer">
      <textarea id="composer-input" placeholder="对 ${escapeHTML(character.name)} 说……" rows="2" ${locked ? "disabled" : ""}></textarea>
      <div class="composer-actions"><button id="continue-turn" class="continue-button" ${data.messages.length && !locked ? "" : "disabled"}>继续这一刻</button><button id="send-turn" class="send-button" aria-label="发送" ${locked ? "disabled" : ""}>↑</button></div>
    </div></div>`;
  $("#send-turn").onclick = () => sendTurn(false);
  $("#continue-turn").onclick = () => sendTurn(true);
  $("#retry-opening")?.addEventListener("click", () => generateOpening());
  $("#composer-input").onkeydown = event => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendTurn(false); }
  };
  requestAnimationFrame(scrollMessages);
}

function messageHTML(message, record = null) {
  const who = message.role === "user" ? "你" : state.character.name;
  return `<article class="message ${message.role} ${message.status === "error" ? "error" : ""}" data-message-id="${message.id}">
    <div class="message-label">${escapeHTML(who)}</div>
    <div class="message-body ${message.status === "streaming" ? "typing-caret" : ""}">${escapeHTML(message.content)}</div>
    ${record ? recordHTML(record) : ""}
  </article>`;
}

function recordHTML(record) {
  const visibility = record.visibility === "public" ? "公开变化" : record.visibility === "secret" ? "秘密记录" : "私下发生";
  return `<aside class="world-record">
    <div class="record-icon"><i></i><i></i></div>
    <div><span>世界记录 · ${visibility}</span><strong>${escapeHTML(record.title)}</strong><p>${escapeHTML(record.details)}</p></div>
    <small>${record.cognition_synced ? "已经传开" : "等待世界同步"}</small>
  </aside>`;
}

function renderCharacterInspector() {
  const data = state.conversation;
  const character = data.character;
  const relationship = relationshipFor(character.id);
  const threads = data.scene.active_threads || [];
  const sceneLocation = state.world.locations.find(item => item.id === data.scene.location_id)?.name || character.location_name || "地点未定";
  $("#inspector").innerHTML = `
    <div class="inspector-title">${escapeHTML(character.name)}的视角</div>
    <div class="inspector-profile">${avatar(character)}<h2>${escapeHTML(character.name)}</h2><p>${escapeHTML(character.role)} · ${character.age}</p></div>
    <div class="inspector-actions"><button class="soft-button" data-action="edit-character">编辑人物</button></div>
    <article class="inspector-card"><h4>当前现场</h4><p>${escapeHTML(sceneLocation)}<br>${escapeHTML(data.scene.physical_state || "距离尚未改变")}<br>${escapeHTML(data.scene.emotional_state || "情绪仍未显露")}</p></article>
    <article class="inspector-card"><h4>与你的关系</h4><p>${escapeHTML(relationship.label || "尚未命名")}<br>${escapeHTML(relationship.notes || "还没有留下共同经历")}</p></article>
    <article class="inspector-card"><h4>还在发生</h4><ul class="thread-list">${threads.length ? threads.map(item => `<li>${escapeHTML(item)}</li>`).join("") : "<li>尚未形成牵引</li>"}</ul></article>
    <article class="inspector-card"><h4>${escapeHTML(character.name)}知道或相信的事</h4><ul class="knowledge-list">${data.knowledge.length ? data.knowledge.slice(0,10).map(item => `<li><span>${escapeHTML(item.belief || item.title)}</span><small>${escapeHTML(knowledgeSource(item))}</small></li>`).join("") : "<li>还没有只属于此人的事实</li>"}</ul></article>`;
}

function knowledgeSource(item) {
  if (item.state === "misunderstood") return "他目前相信的版本";
  if (item.state === "heard") return "后来听闻";
  return item.source || "亲历";
}

function metric(name, value, max = 100) {
  const number = Number(value) || 0;
  const normalized = clamp((number / Math.max(1, max)) * 100);
  return `<div class="metric"><div class="metric-head"><span>${escapeHTML(name)}</span><b>${number}</b></div><div class="metric-track"><i style="width:${normalized}%"></i></div></div>`;
}

async function sendTurn(continuation) {
  if (state.streaming || state.openingConversation) return;
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
      body:JSON.stringify({content, continuation}),
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
          const followOutput = isNearMessagesEnd(messages);
          $(".message-body", assistantNode).textContent = event.full;
          if (followOutput) scrollMessages();
        }
        if (event.type === "world_patch" && event.result?.record && assistantNode) {
          const followOutput = isNearMessagesEnd(messages);
          assistantNode.insertAdjacentHTML("beforeend", recordHTML(event.result.record));
          if (followOutput) scrollMessages();
        }
        if (event.type === "error") {
          if (assistantNode) {
            assistantNode.classList.add("error");
            $(".message-body", assistantNode).classList.remove("typing-caret");
            if (!event.partial) $(".message-body", assistantNode).textContent = event.error.message;
          }
          throw new Error(event.error.message);
        }
        if (event.type === "patch_skipped") toast(event.message);
      }
      if (done) break;
    }
    await refreshWorld();
    await reloadConversation();
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
  [$("#send-turn"), $("#continue-turn"), $("#composer-input")].filter(Boolean).forEach(node => node.disabled = disabled);
}

function scrollMessages() { const node = $("#messages"); if (node) node.scrollTop = node.scrollHeight; }
function isNearMessagesEnd(node) { return node.scrollHeight - node.scrollTop - node.clientHeight < 120; }

function showView(view) {
  state.view = view;
  $("#world-view").classList.toggle("active", view === "world");
  $("#chat-view").classList.toggle("active", view === "chat");
  $("#room-view").classList.toggle("active", view === "room");
  $(".nav-button").classList.toggle("active", view === "world");
  $("#mobile-back").classList.toggle("visible", view === "chat" || view === "room");
  updateHeader();
}

function showWorldHome() {
  if (!state.world) return;
  clearTimeout(openWorld.introTimer);
  state.playingWorldIntro = false;
  state.openingConversation = false;
  state.character = null;
  state.conversation = null;
  state.room = null;
  showView("world");
  renderShell();
  renderWorld();
  renderWorldInspector();
}

function updateHeader() {
  if (!state.world) return;
  const title = state.view === "chat" && state.character ? state.character.name
    : state.view === "room" && state.room ? state.room.room.name
    : state.world.world.name;
  $("#stage-title").textContent = title;
  $("#stage-eyebrow").textContent = state.view === "chat" ? "私人现场 · 共享后果" : state.view === "room" ? "同场 · 各说各话" : "共享世界";
}

function renderProviderState() {
  const connected = Boolean(state.provider?.has_key && state.provider?.model);
  for (const node of [$("#provider-state"), $("#library-provider-state")]) {
    if (!node) continue;
    node.classList.toggle("online", connected);
    $("span", node).textContent = connected ? state.provider.model : "未连接";
  }
}

function fillProviderForm() {
  const form = $("#provider-form");
  const profile = state.provider || {protocol:"openai-compatible",base_url:"https://api.openai.com",path:"/v1/chat/completions",model:"",max_tokens:16384,temperature:.95};
  for (const key of ["protocol","base_url","path","model","max_tokens","temperature"]) if (form.elements[key]) form.elements[key].value = profile[key] ?? "";
  form.elements.api_key.value = "";
}

function openSettings() { fillProviderForm(); $("#provider-feedback").textContent = ""; $("#provider-dialog").showModal(); }
function closeSettings() { $("#provider-dialog").close(); }

const velvetDefaults = {
  name:"绯港", premise:"一座把秘密留得比人更久的海港。三名彼此牵连的成年人，各自与你拥有独立关系，却生活在同一条持续变化的时间线上。",
  opening_narration:"第七码头的钟声还没有响，雾压在绯港的屋檐下。夜航公馆仍亮着一扇窗，鸦色会所最后一轮酒还没有收，十三号码头的临时诊室也没有熄灯。今晚的三处相遇都还在等你走近。",
  atmosphere:"潮湿、华丽、危险、亲密",
  rules:"所有角色均为成年人；角色拥有自主意志；发生过的事会改变关系、知识与之后的选择；意图不是事实。",
  current_time:"第七码头钟响前",
};

function selectTemplate(templateId, fill = true) {
  const form = $("#world-form");
  form.elements.template_id.value = templateId;
  $$('[data-template]').forEach(button => button.classList.toggle("active", button.dataset.template === templateId));
  if (!fill) return;
  const values = templateId === "velvet_harbor" ? velvetDefaults : {name:"", premise:"", opening_narration:"", atmosphere:"", rules:"所有角色均为成年人；角色拥有自主意志；意图不是事实。", current_time:""};
  for (const key of ["name","premise","opening_narration","atmosphere","rules","current_time"]) form.elements[key].value = values[key];
}

function openWorldEditor(isNew = false) {
  state.editingWorldId = isNew ? null : state.world.world.id;
  const form = $("#world-form");
  const world = isNew ? {name:"",premise:"",opening_narration:"",atmosphere:"",rules:"所有角色均为成年人；角色拥有自主意志；意图不是事实。",current_time:""} : state.world.world;
  $("#world-dialog-title").textContent = isNew ? "开启新世界" : "世界底稿";
  $("#world-template-picker").style.display = isNew ? "grid" : "none";
  selectTemplate("", false);
  for (const key of ["name","premise","opening_narration","atmosphere","rules","current_time"]) form.elements[key].value = world[key] || "";
  $("#location-editor").style.display = isNew ? "none" : "block";
  $("#editor-location-list").innerHTML = isNew ? "" : state.world.locations.map(item => `<span>${escapeHTML(item.name)}</span>`).join("");
  $("#new-location-name").value = ""; $("#new-location-mood").value = ""; $("#new-location-description").value = "";
  $("#world-dialog").showModal();
}

function closeWorldEditor() { $("#world-dialog").close(); }

function openCharacterEditor(isNew = false) {
  state.editingCharacterId = isNew ? null : state.character?.id;
  const form = $("#character-form");
  const character = isNew ? {name:"",age:25,pronouns:"",role:"",tagline:"",opening_hook:"",persona:"",speech_style:"",signature_behaviors:[],loss_of_control:"",desire_profile:{control:50,devotion:50,jealousy:50,risk:50,tenderness:50},accent:"#bc5d45",current_location_id:state.world.locations[0]?.id || ""} : state.world.characters.find(item => item.id === state.editingCharacterId);
  if (!character && !isNew) return;
  $("#character-dialog-title").textContent = isNew ? "新人物" : "人物底色";
  for (const key of ["name","age","pronouns","role","tagline","opening_hook","persona","speech_style","loss_of_control","accent"]) form.elements[key].value = character[key] ?? "";
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
  const body = Object.fromEntries(new FormData(event.currentTarget));
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
  const template = event.target.closest("[data-template]")?.dataset.template;
  if (template !== undefined) selectTemplate(template);
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "settings") openSettings();
  if (action === "close-settings") closeSettings();
  if (action === "library") showLibrary();
  if (action === "world-home") showWorldHome();
  if (action === "edit-world") openWorldEditor(false);
  if (action === "new-world") openWorldEditor(true);
  if (action === "close-world-editor") closeWorldEditor();
  if (action === "new-character") openCharacterEditor(true);
  if (action === "edit-character") openCharacterEditor(false);
  if (action === "close-character-editor") closeCharacterEditor();
  if (action === "new-room") openRoomDialog();
  if (action === "close-room-dialog") closeRoomDialog();
  if (action === "close-room") showWorldHome();
});

$("#room-form").onsubmit = async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const characterIds = [...$("#room-character-picker").querySelectorAll("input:checked")].map(input => input.value);
  if (!characterIds.length) { toast("先拉至少一个人入局"); return; }
  try {
    const room = await api(`/api/worlds/${state.world.world.id}/rooms`, {
      method: "POST",
      body: JSON.stringify({
        name: form.elements.name.value.trim() || "同场",
        location_id: form.elements.location_id.value || null,
        character_ids: characterIds,
      }),
    });
    closeRoomDialog();
    await refreshWorld();
    await openRoom(room.room.id);
    toast("同场房间已开启");
  } catch (error) { toast(error.message); }
};

$("#world-form").onsubmit = async event => {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(event.currentTarget));
  if (state.editingWorldId) delete body.template_id;
  try {
    const bundle = state.editingWorldId
      ? await api(`/api/worlds/${state.editingWorldId}`, {method:"PUT", body:JSON.stringify(body)})
      : await api("/api/worlds", {method:"POST", body:JSON.stringify(body)});
    closeWorldEditor();
    await refreshBootstrap();
    await openWorld(bundle.world.id);
    toast("世界已保存");
  } catch (error) { toast(error.message); }
};

$("#add-location").onclick = async () => {
  const name = $("#new-location-name").value.trim();
  if (!name || !state.editingWorldId) return;
  try {
    await api(`/api/worlds/${state.editingWorldId}/locations`, {method:"POST", body:JSON.stringify({name,mood:$("#new-location-mood").value.trim(),description:$("#new-location-description").value.trim()})});
    await refreshWorld();
    $("#editor-location-list").innerHTML = state.world.locations.map(item => `<span>${escapeHTML(item.name)}</span>`).join("");
    $("#new-location-name").value = ""; $("#new-location-mood").value = ""; $("#new-location-description").value = "";
    renderWorldInspector(); toast("地点已加入");
  } catch (error) { toast(error.message); }
};

$("#character-form").onsubmit = async event => {
  event.preventDefault();
  const raw = Object.fromEntries(new FormData(event.currentTarget));
  const body = {
    name:raw.name, age:Number(raw.age), pronouns:raw.pronouns, role:raw.role, tagline:raw.tagline, opening_hook:raw.opening_hook,
    persona:raw.persona, speech_style:raw.speech_style, loss_of_control:raw.loss_of_control,
    current_location_id:raw.current_location_id || null, accent:raw.accent,
    signature_behaviors:raw.signature_behaviors.split("\n").map(item => item.trim()).filter(Boolean),
    desire_profile:Object.fromEntries(["control","devotion","jealousy","risk","tenderness"].map(key => [key,clamp(raw[key])])),
  };
  const editingId = state.editingCharacterId;
  try {
    const character = editingId
      ? await api(`/api/characters/${editingId}`, {method:"PUT", body:JSON.stringify(body)})
      : await api(`/api/worlds/${state.world.world.id}/characters`, {method:"POST", body:JSON.stringify(body)});
    closeCharacterEditor();
    await refreshWorld();
    if (editingId) await openCharacter(character.id); else { showWorldHome(); toast("人物已加入这个世界"); }
  } catch (error) { toast(error.message); }
};

bootstrap().catch(error => toast(error.message));
