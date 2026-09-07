const state = {
  worlds: [], world: null, provider: null,
  screen: "library", view: "world",
  conversation: null, character: null,
  rooms: [], room: null, roomStreaming: false,
  roomWriting: false, roomDrafts: new Map(),
  streamingConversationIds: new Set(), openingConversationIds: new Set(),
  writingConversationIds: new Set(), conversationDrafts: new Map(),
  pulsing: false, resettingWorld: false, recovering: false,
  playingWorldIntro: false,
  editingWorldId: null, editingCharacterId: null,
  navigation: 0, worldRefresh: 0,
  advanceCustom: new Map(), worldActivities: new Map(), liveTurns: new Map(), liveRooms: new Map(),
  viewMemory: new Map(), advanceChoices: new Map(),
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = (value = "") => String(value).replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
const clamp = (value, min = 0, max = 100) => Math.max(min, Math.min(max, Number(value) || 0));

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function worldBusy(worldId = state.world?.world.id) {
  return state.worldActivities.has(worldId);
}

function setActivity(worldId, label = "") {
  if (label) state.worldActivities.set(worldId, label);
  else state.worldActivities.delete(worldId);
  const current = state.worldActivities.get(state.world?.world.id) || "";
  const status = $("#activity-status");
  status.textContent = current;
  status.classList.toggle("visible", Boolean(current));
}

function beginNavigation() {
  rememberCurrentView();
  const token = ++state.navigation;
  document.body.classList.add("navigating");
  return token;
}

function finishNavigation(token) {
  if (token === state.navigation) document.body.classList.remove("navigating");
}

function viewKey(view = state.view) {
  if (view === "chat") return state.conversation ? `chat:${state.conversation.conversation.id}` : "";
  if (view === "room") return state.room ? `room:${state.room.room.id}` : "";
  return state.world ? `world:${state.world.world.id}` : "";
}

function rememberView(root) {
  if (!root?.dataset.viewKey || !root.classList.contains("active") || state.screen !== "world") return;
  const scroller = $(".messages", root) || root;
  const input = $(".composer textarea", root);
  const openDetails = [...root.querySelectorAll("details[open]")].map(detail => {
    const message = detail.closest("[data-message-id], [data-room-message-id]");
    return `${message?.dataset.messageId || message?.dataset.roomMessageId || ""}:${detail.className}`;
  });
  state.viewMemory.set(root.dataset.viewKey, {
    top: scroller.scrollTop, end: isNearMessagesEnd(scroller), openDetails,
    focused: document.activeElement === input,
    start: input?.selectionStart, finish: input?.selectionEnd,
  });
}

function rememberCurrentView() {
  if (state.screen === "world") rememberView($(`#${state.view}-view`));
}

function restoreView(root, key) {
  root.dataset.viewKey = key;
  const memory = state.viewMemory.get(key);
  const scroller = $(".messages", root) || root;
  const input = $(".composer textarea", root);
  resizeComposer(input);
  for (const detail of root.querySelectorAll("details")) {
    const message = detail.closest("[data-message-id], [data-room-message-id]");
    const id = `${message?.dataset.messageId || message?.dataset.roomMessageId || ""}:${detail.className}`;
    if (memory?.openDetails.includes(id)) detail.open = true;
  }
  requestAnimationFrame(() => {
    if (root.dataset.viewKey !== key) return;
    scroller.scrollTop = memory && !memory.end ? memory.top : (scroller === root ? memory?.top || 0 : scroller.scrollHeight);
    if (input && memory?.focused && root.classList.contains("active") && !input.disabled && !$("dialog[open]")) {
      input.focus({preventScroll:true});
      input.setSelectionRange(memory.start ?? input.value.length, memory.finish ?? input.value.length);
    }
    updateLatestButton(root);
  });
  scroller.onscroll = () => {
    rememberView(root);
    updateLatestButton(root);
  };
}

function updateLatestButton(root) {
  const scroller = $(".messages", root);
  $(".latest-button", root)?.classList.toggle("visible", Boolean(scroller && !isNearMessagesEnd(scroller)));
}

function resizeComposer(input) {
  if (!input) return;
  input.style.height = "auto";
  input.style.height = `${Math.min(156, Math.max(46, input.scrollHeight))}px`;
}

function bindComposer(input, drafts, id, send) {
  input.oninput = () => { drafts.set(id, input.value); resizeComposer(input); };
  input.onkeydown = event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      send();
    }
  };
}

function showDialog(id) {
  const dialog = $(id);
  dialog.classList.remove("closing");
  if (!dialog.open) dialog.showModal();
  dialog.scrollTop = 0;
}

function closeDialog(id) {
  const dialog = $(id);
  if (!dialog.open) return;
  if (dialog.dataset.busy === "true") { dialog.dataset.closeAfterBusy = "true"; return; }
  if (reducedMotion.matches) { dialog.close(); return; }
  dialog.classList.add("closing");
  setTimeout(() => { dialog.close(); dialog.classList.remove("closing"); }, 140);
}

function setInspector(open) {
  if (open) setInspector.previousFocus = document.activeElement;
  const wasOpen = $("#inspector").classList.contains("open");
  $(".main-stage").inert = open;
  $(".left-rail").inert = open;
  $("#inspector").classList.toggle("open", open);
  $("#inspector-scrim").classList.toggle("visible", open);
  $$(".inspector-toggle").forEach(button => button.setAttribute("aria-expanded", String(open)));
  if (open) $("#close-inspector").focus({preventScroll:true});
  else if (wasOpen && setInspector.previousFocus?.isConnected) setInspector.previousFocus.focus({preventScroll:true});
}

async function formAction(form, button, label, action) {
  if (form.dataset.busy === "true") return;
  const dialog = form.closest("dialog");
  form.dataset.busy = "true";
  dialog.dataset.busy = "true";
  const previous = button.textContent;
  const buttons = [...form.querySelectorAll("button")].map(node => [node, node.disabled]);
  buttons.forEach(([node]) => node.disabled = true);
  button.textContent = label;
  button.setAttribute("aria-busy", "true");
  try { await action(); }
  finally {
    form.dataset.busy = "false"; dialog.dataset.busy = "false";
    buttons.forEach(([node, disabled]) => node.disabled = disabled);
    button.textContent = previous; button.removeAttribute("aria-busy");
    if (dialog.dataset.closeAfterBusy === "true") {
      delete dialog.dataset.closeAfterBusy; closeDialog(`#${dialog.id}`);
    }
  }
}

function mergeLive(data, live) {
  if (!live) return data;
  const messages = new Map(data.messages.map(message => [message.id, message]));
  live.messages.forEach((message, id) => messages.set(id, {...messages.get(id), ...message}));
  return {...data, messages:[...messages.values()],
    records:[...data.records.filter(item => !live.records.some(record => record.id === item.id)), ...live.records],
    settlements:[...data.settlements.filter(item => !live.settlements.some(entry => entry.id === item.id)), ...live.settlements]};
}

const DESIRE_BANDS = [[80, "难以自持"], [60, "清晰可感"], [40, "收放之间"], [20, "若隐若现"], [0, "几近于无"]];
function desireHint(value) {
  const number = clamp(value);
  return DESIRE_BANDS.find(([min]) => number >= min)[1];
}
function refreshDesireHints() {
  $$("#character-form .desire-editor input[type=range]").forEach(input => {
    const hint = input.closest("label").querySelector(".desire-hint");
    if (hint) hint.textContent = desireHint(input.value);
  });
}

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

async function readStream(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "", completed = false;
  const consume = line => {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.type === "done") completed = true;
    onEvent(event);
  };
  try {
    while (true) {
      const {value, done} = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), {stream:!done});
      const lines = buffer.split("\n"); buffer = lines.pop() || "";
      lines.forEach(consume);
      if (done) { consume(buffer); break; }
    }
    if (!completed) throw new Error("回应连接中断，已保存的内容仍在");
  } finally {
    if (!completed) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
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
  const cards = state.worlds.map((world, index) => `
    <button class="library-world-card" style="--world-order:${index}" data-open-world="${world.id}">
      <span class="world-card-number">WORLD ${String(index + 1).padStart(2, "0")}</span>
      <span class="world-card-kicker">${escapeHTML(world.atmosphere || "尚待落笔")}</span>
      <strong>${escapeHTML(world.name)}</strong>
      <p>${escapeHTML(world.premise || "这个世界还没有写下前提。")}</p>
      <div class="world-card-meta">
        <span>${world.character_count || 0} 人</span>
        <span>${escapeHTML(world.current_time || "时间未定")}</span>
      </div>
      <div class="world-card-last">${world.latest_public_event ? `最近：${escapeHTML(world.latest_public_event)}` : "还没有公开的大事发生"}</div>
      ${world.pending_sync_count ? `<em>${world.pending_sync_count} 件变化待同步</em>` : ""}
      <span class="world-card-arrow" aria-hidden="true">↗</span>
    </button>`).join("");
  grid.innerHTML = cards + `
    <button class="new-world-card" data-action="new-world">
      <span>＋</span><strong>开启新世界</strong><small>空白世界书或预制序章</small>
    </button>`;
  $$('[data-open-world]').forEach(card => card.onclick = () => openWorld(card.dataset.openWorld));
}

function showLibrary(refresh = true) {
  rememberCurrentView();
  ++state.navigation;
  document.body.classList.remove("navigating");
  setInspector(false);
  state.screen = "library";
  $("#library-screen").classList.remove("hidden");
  $("#world-shell").classList.add("hidden");
  $("#world-shell").classList.remove("crossing");
  if (refresh) refreshBootstrap().catch(error => toast(error.message));
}

async function openWorld(worldId) {
  const token = beginNavigation();
  try {
    const [world, roomsData] = await Promise.all([
      api(`/api/worlds/${worldId}`), api(`/api/worlds/${worldId}/rooms`),
    ]);
    if (token !== state.navigation) return;
    state.world = world; state.rooms = roomsData.rooms || [];
    state.conversation = null; state.character = null; state.room = null;
    state.screen = "world"; state.view = "world";
    state.playingWorldIntro = !reducedMotion.matches;
    clearTimeout(openWorld.introTimer);
    $("#library-screen").classList.add("hidden");
    $("#world-shell").classList.remove("hidden");
    renderShell(); renderWorld(); renderWorldInspector(); showView("world");
    openWorld.introTimer = setTimeout(() => {
      if (state.world?.world.id !== worldId) return;
      state.playingWorldIntro = false;
      $("#world-view .world-wrap")?.classList.remove("entering");
    }, 1000);
  } catch (error) { if (token === state.navigation) toast(error.message); }
  finally { finishNavigation(token); }
}

async function refreshWorld(worldId = state.world?.world.id) {
  if (!worldId) return;
  const revision = ++state.worldRefresh;
  const [world, roomsData] = await Promise.all([
    api(`/api/worlds/${worldId}`), api(`/api/worlds/${worldId}/rooms`),
  ]);
  if (state.world?.world.id !== worldId || revision !== state.worldRefresh) return;
  state.world = world; state.rooms = roomsData.rooms || [];
  if (state.character) state.character = world.characters.find(item => item.id === state.character.id) || state.character;
  renderShell();
  if (state.view === "world") { renderWorld(); renderWorldInspector(); }
}

function renderShell() {
  const {world, characters} = state.world;
  $("#world-name-side").textContent = world.name;
  $("#world-clock").textContent = world.current_time || "时间未定";
  $("#character-count").textContent = characters.length;
  $("#character-list").innerHTML = characters.map(character => {
    const unavailable = character.availability === "unavailable";
    return `
    <button class="character-button ${state.character?.id === character.id ? "active" : ""} ${unavailable ? "unavailable" : ""}" data-character="${character.id}" ${unavailable ? "disabled" : ""}>
      ${avatar(character)}
      <span><strong>${escapeHTML(character.name)}</strong><small>${escapeHTML(character.role)}</small></span>
      <i class="presence" style="--accent:${/^#[0-9a-f]{6}$/i.test(character.accent || "") ? character.accent : "#bc5d45"}"></i>
    </button>`;
  }).join("");
  $$('[data-character]').forEach(button => button.onclick = () => openCharacter(button.dataset.character));
  updateHeader();
  setActivity(state.world.world.id, state.worldActivities.get(state.world.world.id) || "");
}

function pendingSettlements() {
  const data = state.view === "chat" ? state.conversation : state.view === "room" ? state.room : state.world;
  return [...new Map([...(state.world?.pending_settlements || []), ...(data?.pending_settlements || [])].map(job => [job.id, job])).values()];
}

function settlementRecoveryHTML(job = null) {
  if (!job && !pendingSettlements().length) return "";
  return `<div class="settlement-recovery" role="status"><span>${state.recovering ? "世界正在留下痕迹" : "还有未记下的余波"}</span><button class="soft-button" data-action="retry-settlements" ${state.recovering || worldBusy() ? "disabled" : ""}>${state.recovering ? "正在记录…" : "重新记录"}</button></div>`;
}

async function retrySettlements() {
  if (state.recovering || worldBusy()) return;
  const worldId = state.world.world.id;
  state.recovering = true; setActivity(worldId, "世界正在留下痕迹"); renderCurrent();
  try {
    const result = await api(`/api/worlds/${worldId}/settlements/retry`, {method:"POST", body:"{}"});
    if (result.pending_settlements.length) toast("这段余波仍未记下，可以再次重试");
    else toast("余波已经记下");
  } catch (error) { toast(error.message); }
  finally {
    await refreshWorld(worldId).catch(() => {});
    if (state.world?.world.id === worldId) {
      if (state.conversation) await reloadConversation().catch(() => {});
      if (state.room) {
        const roomId = state.room.room.id;
        const data = await api(`/api/rooms/${roomId}`).catch(() => null);
        if (data && state.room?.room.id === roomId) state.room = data;
      }
    }
    state.recovering = false; setActivity(worldId); renderCurrent();
  }
}

function renderWorld() {
  rememberView($("#world-view"));
  const {world, locations, characters, events, pending_sync_count: pending} = state.world;
  const player = state.world.player || {};
  const pulse = state.world.pulse || {};
  const playerTraces = player.visible_traces || [];
  const playerPromises = player.commitments || [];
  const encounterGroups = [
    ["present", "眼前的人"],
    ["reachable", "可以去见的人"],
    ["incoming", "正在靠近的人"],
    ["unavailable", "暂时错过"],
  ];
  let cardIndex = 0;
  const encounterSections = encounterGroups.map(([key, title]) => {
    const members = characters.filter(character => effectiveAvailability(character, player) === key);
    if (!members.length) return "";
    const cards = members.map(character => characterCard(character, cardIndex++, player)).join("");
    return `<div class="encounter-group"><div class="encounter-group-head">${title}</div><div class="world-character-grid">${cards}</div></div>`;
  }).join("");
  $("#world-view").innerHTML = `<div class="world-wrap ${state.playingWorldIntro ? "entering" : ""}">
    <article class="world-hero">
      <div class="world-hero-copy"><span class="stage-eyebrow">此刻发生</span><h2>${escapeHTML(world.current_time || world.name)}</h2><p>${escapeHTML(pulse.narration || world.opening_narration || world.premise || "这一刻还没有落笔。")}</p></div>
      <div class="world-hero-time"><small>${escapeHTML(world.atmosphere || "共享世界")}</small><strong>${escapeHTML(world.name)}</strong></div>
      <div class="world-gate" aria-hidden="true"><i></i><i></i><i></i><span></span></div>
    </article>

    ${settlementRecoveryHTML()}
    <section class="player-presence world-secondary">
      <div class="player-presence-mark"><i></i><span>你</span></div>
      <div class="player-presence-main"><span class="stage-eyebrow">此刻的你</span><h3>${escapeHTML(player.location_name || "尚未走进某个现场")}</h3><p>${escapeHTML(player.condition || "还没有留下足以被旁人察觉的变化。")}</p></div>
      <div class="player-presence-details">
        ${playerTraces.length ? `<div><small>带在身上的痕迹</small>${playerTraces.map(item => `<span>${escapeHTML(item)}</span>`).join("")}</div>` : ""}
        ${playerPromises.length ? `<div><small>还悬着的承诺</small>${playerPromises.map(promiseHTML).join("")}</div>` : ""}
      </div>
    </section>

    <div class="section-head character-reveal-head"><div><span class="stage-eyebrow">此刻的相遇</span><h3>谁正在世界里等你</h3></div><button class="text-button" data-action="new-character">＋ 添加人物</button></div>
    ${characters.length ? encounterSections : `<div class="world-character-grid"><button class="empty-character-card" data-action="new-character">写下这个世界的第一个人物</button></div>`}

    <div class="section-head character-reveal-head"><div><span class="stage-eyebrow">SAME SCENE</span><h3>同场房间</h3></div><button class="text-button" data-action="new-room">＋ 新同场</button></div>
    <div class="room-list">${state.rooms.length ? state.rooms.map(roomCard).join("") : `<div class="room-enter">此刻，还没有同场相遇。</div>`}</div>

    <section class="sync-panel world-secondary ${pending ? "waiting" : "quiet"} ${state.pulsing ? "pulsing" : ""}">
      <div class="sync-orbit"><i></i><i></i><i></i></div>
      <div><span class="stage-eyebrow">WORLD PULSE</span><h3>${state.pulsing ? "所有人都在决定下一步" : pending ? `${pending} 件余波正等着世界回应` : "这一刻还可以继续，也可以向前"}</h3></div>
      <button id="pulse-world" class="primary-button" ${worldBusy() || pendingSettlements().length || state.recovering ? "disabled" : ""}>${state.pulsing ? "世界正在向前…" : "让这一刻继续"}</button>
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
    <div class="world-danger-zone world-secondary"><button class="reset-world-button" data-action="reset-world">重置这个世界</button></div>
  </div>`;
  $$('[data-world-character]').forEach(card => card.onclick = () => openCharacter(card.dataset.worldCharacter));
  $$('[data-room]').forEach(card => card.onclick = () => openRoom(card.dataset.room));
  $("#pulse-world")?.addEventListener("click", advanceWorld);
  restoreView($("#world-view"), viewKey("world"));
}

function promiseHTML(item) {
  const character = state.world.characters.find(person => person.id === item.character_id);
  const available = character && effectiveAvailability(character) !== "unavailable";
  return `<span class="player-promise"><span>${escapeHTML(item.text)}</span>${available
    ? `<button data-world-character="${character.id}">去见${escapeHTML(character.name)} ↗</button>`
    : item.character_name ? `<small>${escapeHTML(item.character_name)}</small>` : ""}</span>`;
}

function effectiveAvailability(character, player = state.world?.player || {}) {
  if (player.location_id && character.current_location_id === player.location_id) return "present";
  return character.availability || "reachable";
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
  if (state.screen === "world" && (worldBusy() || pendingSettlements().length)) { toast("还有未记下的余波，请稍候"); return; }
  const form = $("#room-form");
  form.elements.name.value = "";
  const locationSelect = form.elements.location_id;
  locationSelect.innerHTML = '<option value="">未定</option>' + state.world.locations.map(item => `<option value="${item.id}">${escapeHTML(item.name)}</option>`).join("");
  const picker = $("#room-character-picker");
  picker.innerHTML = state.world.characters.map(character => `
    <label><input type="checkbox" value="${character.id}"><span>${avatar(character)}</span>${escapeHTML(character.name)}</label>`).join("");
  showDialog("#room-dialog");
}

function closeRoomDialog() { closeDialog("#room-dialog"); }

async function openRoom(roomId) {
  if (state.view === "room" && state.room?.room.id === roomId) return;
  const worldId = state.world.world.id;
  const token = beginNavigation();
  try {
    const room = await api(`/api/rooms/${roomId}/enter`, {method:"POST", body:"{}"});
    if (token !== state.navigation || state.world?.world.id !== worldId) return;
    if (!worldBusy(worldId)) state.liveRooms.delete(roomId);
    state.room = room; state.world.player = room.player;
    state.conversation = null; state.character = null; state.view = "room";
    renderShell(); renderRoom(); renderRoomInspector(); showView("room");
  } catch (error) { if (token === state.navigation) toast(error.message); }
  finally { finishNavigation(token); }
}

function renderRoom() {
  rememberView($("#room-view"));
  const data = mergeLive(state.room, state.liveRooms.get(state.room.room.id));
  const room = data.room;
  const inputLocked = state.liveRooms.has(room.id) && !state.liveRooms.get(room.id).drafting;
  const turnLocked = worldBusy() || pendingSettlements().length || state.recovering;
  const draft = state.roomDrafts.get(room.id) || "";
  const recordsByMessage = Object.fromEntries(
    (data.records || []).map(record => [record.source_room_message_id, record])
  );
  const settlementsByMessage = Object.fromEntries(
    (data.settlements || []).map(settlement => [settlement.source_room_message_id, settlement])
  );
  $("#room-view").innerHTML = `
    <div class="room-toolbar">
      <button class="chat-back" data-action="world-home">‹ 世界</button>
      <div class="chat-person"><div><strong>${escapeHTML(room.name)}</strong><small>${escapeHTML(data.location?.name || "地点未定")}</small></div></div>
      <div class="room-members">${data.characters.map(character => avatar(character)).join("")}</div>
      <span class="live-world-badge">同场</span>
      <button class="inspector-toggle" data-action="toggle-inspector" aria-expanded="false" aria-controls="inspector">在场的人</button>
    </div>
    <div id="room-messages" class="messages" role="log" aria-live="polite" aria-atomic="false">${data.messages.length ? data.messages.map(message => roomMessageHTML(message, recordsByMessage[message.id], settlementsByMessage[message.id])).join("") : `<div class="empty-state opening"><p>${escapeHTML(room.name)} · 等你开口</p></div>`}</div>
    ${state.liveRooms.has(room.id) ? '<div class="room-waiting"><span class="reply-waiting"><i></i><i></i><i></i></span> 同场正在继续</div>' : ""}
    <button class="latest-button" data-action="latest">回到最新 ↓</button>
    ${data.messages.some(message => message.settlement_job) ? "" : settlementRecoveryHTML()}
    <div class="composer-shell"><div class="composer">
      <textarea id="room-composer-input" placeholder="对在场的人说……" rows="2" ${inputLocked ? "disabled" : ""}>${escapeHTML(draft)}</textarea>
      <div class="composer-actions"><button id="room-send-turn" class="send-button" aria-label="发送" ${turnLocked ? "disabled" : ""}>↑</button></div>
    </div></div>`;
  $("#room-send-turn").onclick = () => sendRoomTurn();
  bindComposer($("#room-composer-input"), state.roomDrafts, room.id, sendRoomTurn);
  restoreView($("#room-view"), viewKey("room"));
}

function roomMessageHTML(message, record = null, settlement = null) {
  const who = message.role === "user" ? "你" : (message.character_name || "在场的人");
  const accent = /^#[0-9a-f]{6}$/i.test(message.character_accent || "") ? message.character_accent : "#bc5d45";
  const label = message.role === "user"
    ? `<div class="message-label">你</div>`
    : `<div class="message-label"><span class="avatar" style="--accent:${accent}">${escapeHTML((message.character_name || "·").slice(0, 1))}</span> ${escapeHTML(who)}</div>`;
  return `<article class="message ${message.role}" data-room-message-id="${message.id}">${label}<div class="message-body">${escapeHTML(message.content)}</div>${recordHTML(record, settlement)}${message.writing ? worldWritePendingHTML() : ""}${message.settlement_job && !message.writing ? settlementRecoveryHTML(message.settlement_job) : ""}</article>`;
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
    <article class="inspector-card"><h4>此刻</h4><p>${escapeHTML(data.scene.physical_state || "众人仍在此处。")}</p></article>`;
  $("#inspector").insertAdjacentHTML("afterbegin", '<button id="close-inspector" class="inspector-close icon-button" data-action="close-inspector" aria-label="关闭视角">×</button>');
}

async function sendRoomTurn() {
  if (!state.room || worldBusy() || pendingSettlements().length || state.recovering) return;
  const input = $("#room-composer-input");
  const content = input.value.trim();
  if (!content) return;
  const roomId = state.room.room.id, worldId = state.world.world.id;
  const characters = state.room.characters;
  const live = {messages:new Map(), records:[], settlements:[], phase:"responding"};
  state.liveRooms.set(roomId, live);
  state.roomStreaming = true; state.roomWriting = false;
  state.roomDrafts.delete(roomId); input.value = "";
  setActivity(worldId, "在场的人正在回应");
  renderRoom();
  const isCurrent = () => state.room?.room.id === roomId;
  const repaint = () => { if (isCurrent()) renderRoom(); };
  let acknowledged = false;
  const failed = [], silent = [];
  try {
    const response = await fetch(`/api/rooms/${roomId}/turn`, {
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({content, continuation:false}),
    });
    if (!response.ok || !response.body) throw new Error("没有接到房间的回应");
    await readStream(response, event => {
      if (event.type === "user") { acknowledged = true; live.messages.set(event.message.id, event.message); repaint(); }
      if (event.type === "room_reply") {
        live.messages.set(event.message.id, {...event.message, writing:true});
        state.roomWriting = true; live.phase = "writing"; live.drafting = true;
        setActivity(worldId, "世界正在留下痕迹"); repaint();
      }
      if (event.type === "room_silent") { silent.push(event.character_name); }
      if (event.type === "world_patch" || event.type === "patch_skipped") {
        const id = event.source_message_id;
        const message = live.messages.get(id);
        if (message) message.writing = false;
        if (event.type === "world_patch") {
          if (event.result.record) live.records.push({...event.result.record, source_room_message_id:id});
          if (event.result.settlement) live.settlements.push(event.result.settlement);
        } else if (message) message.settlement_job = {status:"failed", reason:event.reason};
        live.phase = "responding";
        setActivity(worldId, "同场正在继续"); repaint();
      }
      if (event.type === "error") {
        if (!event.character_id) throw new Error(event.error.message);
        failed.push(characters.find(item => item.id === event.character_id)?.name || "一名角色");
      }
    });
    if (failed.length) toast(`${failed.join("、")}暂时没有回应`);
    else if (silent.length) toast(`${silent.join("、")}留在沉默里`);
  } catch (error) {
    if (!acknowledged) state.roomDrafts.set(roomId, content);
    toast(error.message);
  } finally {
    try {
      const data = await api(`/api/rooms/${roomId}`);
      state.liveRooms.delete(roomId);
      if (isCurrent()) state.room = data;
    } catch {}
    await refreshWorld(worldId).catch(() => {});
    state.roomStreaming = false; state.roomWriting = false;
    setActivity(worldId);
    renderCurrent();
  }
}

function characterCard(character, index = 0, player = state.world?.player || {}) {
  const relationship = relationshipFor(character.id);
  const availability = effectiveAvailability(character, player);
  const availabilityText = {
    present: "就在眼前", incoming: "正在靠近", reachable: "可以去见", unavailable: "暂时错过",
  }[availability];
  const pulseHook = Number(character.pulse_number || 0) > 0 || character.interacted_since_pulse
    ? character.encounter_hook
    : "";
  const summary = pulseHook || (character.has_messages
    ? (character.state_summary || character.tagline || "还没有留下此刻的姿态。")
    : (character.opening_hook || character.tagline || "此刻可以去见他。"));
  const unavailable = availability === "unavailable";
  return `<button class="world-character-card ${availability}" style="--card-index:${index}" data-world-character="${character.id}" ${unavailable ? "disabled" : ""}>
    <div class="encounter-state">${escapeHTML(availabilityText)}</div>
    <div class="character-card-head">${avatar(character, "large")}<div><h4>${escapeHTML(character.name)}</h4><small>${escapeHTML(character.role)} · ${escapeHTML(character.location_name || "地点未定")}</small></div><span>${unavailable ? "未能相遇" : "进入 ›"}</span></div>
    <p>${escapeHTML(summary)}</p>
    <div class="relationship-line"><span>${escapeHTML(relationship.label || "关系尚未命名")}</span><i></i><strong>${escapeHTML(relationship.notes || "还没有留下共同经历")}</strong></div>
  </button>`;
}

function renderWorldInspector() {
  const {world, characters, locations, events, pending_sync_count} = state.world;
  const player = state.world.player || {};
  $("#inspector").innerHTML = `<div class="world-inspector">
    <div class="inspector-title">世界底稿</div>
    <h2>${escapeHTML(world.name)}</h2><p>${escapeHTML(world.rules)}</p>
    <div class="inspector-actions"><button class="soft-button" data-action="edit-world">编辑世界书</button><button class="soft-button" data-action="new-character">新人物</button></div>
    <div class="world-counts"><span><b>${characters.length}</b>人物</span><span><b>${locations.length}</b>地点</span><span><b>${events.length}</b>公开大事</span></div>
    <article class="inspector-card"><h4>此刻的你</h4><p>${escapeHTML(player.location_name || "位置尚未确定")}<br>${escapeHTML(player.condition || "没有明显的外部变化")}<br>${state.world.pending_settlements?.length ? "还有未记下的余波。" : pending_sync_count ? `${pending_sync_count} 件变化会在世界向前时传播。` : "此刻的余波已经记下。"}</p></article>
    <button class="reset-world-button" data-action="reset-world">重置这个世界</button>
  </div>`;
  $("#inspector").insertAdjacentHTML("afterbegin", '<button id="close-inspector" class="inspector-close icon-button" data-action="close-inspector" aria-label="关闭视角">×</button>');
}

async function advanceWorld() { await runWorldPulse(""); }

function renderCurrent() {
  if (!state.world) return;
  if (state.view === "chat" && state.conversation) { renderChat(); renderCharacterInspector(); }
  else if (state.view === "room" && state.room) { renderRoom(); renderRoomInspector(); }
  else { renderWorld(); renderWorldInspector(); }
}

async function runWorldPulse(targetTime) {
  if (!state.world || worldBusy() || pendingSettlements().length) return;
  const worldId = state.world.world.id, token = state.navigation;
  state.pulsing = true; setActivity(worldId, "世界正在向前"); renderCurrent();
  try {
    const result = await api(`/api/worlds/${worldId}/pulse`, {method:"POST", body:JSON.stringify({target_time:targetTime})});
    await refreshWorld(worldId);
    if (state.world?.world.id === worldId && token === state.navigation && state.screen === "world") {
      showWorldHome();
      state.playingWorldIntro = !reducedMotion.matches; renderWorld();
      clearTimeout(advanceWorld.revealTimer);
      advanceWorld.revealTimer = setTimeout(() => {
        state.playingWorldIntro = false;
        $("#world-view .world-wrap")?.classList.remove("entering");
      }, 1000);
      toast(result.world_time ? `世界来到${result.world_time}` : "世界向前了一步");
    }
  } catch (error) { toast(error.message); }
  finally { state.pulsing = false; setActivity(worldId); renderCurrent(); }
}

async function advanceConversationWorld() {
  const select = $("#advance-select");
  const targetTime = select.value === "__custom__" ? $("#custom-time").value.trim() : select.value;
  if (!targetTime) { $("#custom-time").focus(); return; }
  await runWorldPulse(targetTime);
}

async function openCharacter(characterId) {
  if (state.view === "chat" && state.character?.id === characterId) return;
  const worldId = state.world.world.id;
  const token = beginNavigation();
  try {
    const data = await api("/api/conversations", {method:"POST", body:JSON.stringify({world_id:worldId, character_id:characterId})});
    if (token !== state.navigation || state.world?.world.id !== worldId) return;
    const conversationId = data.conversation.id;
    if (!state.streamingConversationIds.has(conversationId)) state.liveTurns.delete(conversationId);
    state.conversation = data; state.character = data.character; state.room = null;
    state.world.player = data.player; state.view = "chat";
    renderShell(); renderChat(); renderCharacterInspector(); showView("chat");
    if (data.needs_scene_entry && !data.pending_settlements?.length && !worldBusy() && !state.openingConversationIds.has(conversationId)) generateOpening(conversationId);
  } catch (error) { if (token === state.navigation) toast(error.message); }
  finally { finishNavigation(token); }
}

async function generateOpening(conversationId = state.conversation?.conversation.id) {
  if (!conversationId || worldBusy() || pendingSettlements().length || state.recovering || state.openingConversationIds.has(conversationId)) return;
  const worldId = state.world.world.id;
  setActivity(worldId, "现场正在展开");
  state.openingConversationIds.add(conversationId);
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
    state.openingConversationIds.delete(conversationId);
    setActivity(worldId);
    renderCurrent();
  }
}

async function reloadConversation(conversationId = state.conversation?.conversation.id) {
  if (!conversationId) return;
  const data = await api(`/api/conversations/${conversationId}`);
  if (state.conversation?.conversation.id !== conversationId) return;
  state.conversation = data; state.character = data.character;
  renderChat(); renderCharacterInspector();
}

function renderChat() {
  rememberView($("#chat-view"));
  const data = mergeLive(state.conversation, state.liveTurns.get(state.conversation.conversation.id));
  const character = data.character;
  const conversationId = data.conversation.id;
  const recordsByMessage = Object.fromEntries(data.records.map(record => [record.source_message_id, record]));
  const isOpening = state.openingConversationIds.has(conversationId);
  const hasHistory = data.messages.length > 0;
  const entryStatus = isOpening
    ? `<div class="opening-sequence"><div class="opening-pulse"><i></i><i></i><i></i></div><p>${escapeHTML(character.name)}${hasHistory ? "正在接上新的此刻" : "正在走进这一刻"}</p></div>`
    : data.needs_scene_entry && !pendingSettlements().length
      ? `<div class="empty-state opening"><p>${hasHistory ? "新的此刻还没有接上。" : escapeHTML(character.tagline)}</p><button id="retry-opening" class="soft-button">让${escapeHTML(character.name)}开口</button></div>`
      : "";
  const turnLocked = worldBusy() || state.streamingConversationIds.has(conversationId) || isOpening || data.needs_scene_entry || pendingSettlements().length || state.recovering;
  const inputLocked = (state.streamingConversationIds.has(conversationId)
    && !state.writingConversationIds.has(conversationId)) || isOpening || (data.needs_scene_entry && !pendingSettlements().length);
  const draft = state.conversationDrafts.get(conversationId) || "";
  const settlementsByMessage = Object.fromEntries(
    (data.settlements || []).map(settlement => [settlement.source_message_id, settlement])
  );
  $("#chat-view").innerHTML = `
    <div class="chat-toolbar">
      <button class="chat-back" data-action="world-home">‹ 世界</button>
      <div class="chat-person">${avatar(character)}<div><strong>${escapeHTML(character.name)}</strong><small>${escapeHTML(character.location_name || character.role)}</small></div></div>
      <span class="live-world-badge">世界持续记录</span>
      <button class="inspector-toggle" data-action="toggle-inspector" aria-expanded="false" aria-controls="inspector">视角</button>
    </div>
    <div id="messages" class="messages" role="log" aria-live="polite" aria-atomic="false">${data.messages.map(message => messageHTML(message, recordsByMessage[message.id], settlementsByMessage[message.id])).join("")}${entryStatus}</div>
    <button class="latest-button" data-action="latest">回到最新 ↓</button>
    ${data.messages.some(message => message.settlement_job) ? "" : settlementRecoveryHTML()}
    <div class="composer-shell"><div class="composer">
      <textarea id="composer-input" placeholder="对 ${escapeHTML(character.name)} 说……" rows="2" ${inputLocked ? "disabled" : ""}>${escapeHTML(draft)}</textarea>
      <div class="composer-actions">
        <div class="composer-tools">
          <button id="continue-turn" class="continue-button" title="从刚才停下的瞬间接着写" ${data.messages.length && !turnLocked ? "" : "disabled"}>继续写</button>
          <select id="advance-select" aria-label="推进到之后" ${data.messages.length && !turnLocked ? "" : "disabled"}>
            <option value="过一会儿">过一会儿</option>
            <option value="第二天早上">第二天早上</option>
            <option value="一周后">一周后</option>
            <option value="__custom__">自定义…</option>
          </select>
          <input id="custom-time" class="custom-time hidden" aria-label="希望到达的时刻" placeholder="例如：次日傍晚" value="${escapeHTML(state.advanceCustom.get(conversationId) || "")}" ${turnLocked ? "disabled" : ""}>
          <button id="advance-turn" class="continue-button" title="跳过一段时间，让整个世界向前" ${data.messages.length && !turnLocked && !state.pulsing ? "" : "disabled"}>让世界向前</button>
        </div>
        <button id="send-turn" class="send-button" aria-label="发送" ${turnLocked ? "disabled" : ""}>↑</button>
      </div>
  </div></div>`;
  const input = $("#composer-input");
  $("#send-turn").onclick = () => sendTurn(false);
  $("#continue-turn").onclick = () => sendTurn(true);
  $("#advance-turn").onclick = () => advanceConversationWorld();
  $("#advance-select").onchange = () => {
    state.advanceChoices.set(conversationId, $("#advance-select").value);
    const custom = $("#advance-select").value === "__custom__";
    $("#custom-time").classList.toggle("hidden", !custom);
    if (custom) $("#custom-time").focus();
  };
  $("#retry-opening")?.addEventListener("click", () => generateOpening());
  bindComposer(input, state.conversationDrafts, conversationId, () => sendTurn(false));
  $("#advance-select").value = state.advanceChoices.get(conversationId) || "过一会儿";
  $("#custom-time").classList.toggle("hidden", $("#advance-select").value !== "__custom__");
  $("#custom-time").oninput = event => state.advanceCustom.set(conversationId, event.target.value);
  restoreView($("#chat-view"), viewKey("chat"));
}

function messageHTML(message, record = null, settlement = null) {
  const who = message.role === "user" ? "你" : state.character.name;
  return `${message.scene_entry?.entry_kind === "reentry" ? '<div class="scene-divider">再次相遇</div>' : ""}<article class="message ${message.role} ${message.status === "error" ? "error" : ""}" data-message-id="${message.id}">
    <div class="message-label">${escapeHTML(who)}</div>
    ${message.status === "streaming" && !message.content ? '<span class="reply-waiting" role="status" aria-label="正在回应"><i></i><i></i><i></i></span>' : ""}
    <div class="message-body ${message.status === "streaming" && message.content ? "typing-caret" : ""}">${escapeHTML(message.content)}</div>
    ${recordHTML(record, settlement)}
    ${message.writing ? worldWritePendingHTML() : ""}
    ${message.settlement_job && !message.writing ? settlementRecoveryHTML(message.settlement_job) : ""}
  </article>`;
}

function readableList(value) {
  if (!Array.isArray(value)) return String(value || "").trim();
  return value.map(item => {
    if (typeof item === "string") return item.trim();
    if (item && typeof item === "object") return String(item.text || item.name || "").trim();
    return "";
  }).filter(Boolean).join("、");
}

function settlementHTML(settlement) {
  if (!settlement) return "";
  const scene = settlement.scene || {};
  const relationship = settlement.relationship || {};
  const player = settlement.player || {};
  const rows = [
    ["地点", scene.location || scene.location_name],
    ["现场", scene.physical_state],
    ["情绪", scene.emotional_state],
    ["仍在发生", readableList(scene.active_threads)],
    ["关系", [relationship.label, relationship.note].filter(Boolean).join(" · ")],
    ["你的状态", player.condition],
    ["你身上的痕迹", readableList(player.visible_traces)],
    ["仍要兑现", readableList(player.commitments)],
  ].filter(([, value]) => String(value || "").trim());
  if (!rows.length) return "";
  return `<details class="settlement-fields"><summary>这一轮留下的变化</summary><ul>${rows.map(([label, value]) => `<li><span>${escapeHTML(label)}</span><p>${escapeHTML(value)}</p></li>`).join("")}</ul></details>`;
}

function recordHTML(record = null, settlement = null) {
  const changes = settlementHTML(settlement);
  if (!record && !changes) return "";
  const visibility = record ? (record.visibility === "public" ? "公开变化" : record.visibility === "secret" ? "秘密记录" : "私下发生") : "这一轮的变化";
  const propagation = record && record.visibility === "public"
    ? (record.cognition_synced ? "已经传开" : "等待世界脉搏")
    : record ? "只留在知情者之间" : "已经落在现场";
  const event = record ? `<details class="record-details">
      <summary><span>世界记录 · ${visibility}</span><strong>${escapeHTML(record.title)}</strong></summary>
      <p>${escapeHTML(record.details)}</p>
    </details>` : "";
  return `<aside class="world-record">
    <div class="record-icon"><i></i><i></i></div>
    <div class="record-main">${event}${changes}</div>
    <small>${propagation}</small>
  </aside>`;
}

function worldWritePendingHTML() {
  return `<aside class="world-write-pending" role="status">
    <span class="world-write-sigil" aria-hidden="true"><i></i><i></i><i></i></span>
    <span>世界正在留下痕迹</span>
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
  $("#inspector").insertAdjacentHTML("afterbegin", '<button id="close-inspector" class="inspector-close icon-button" data-action="close-inspector" aria-label="关闭视角">×</button>');
}

function knowledgeSource(item) {
  if (item.state === "misunderstood") return "目前相信的版本";
  if (item.source?.startsWith("{")) {
    try { return JSON.parse(item.source).kind === "told" ? "被告知" : "亲历"; } catch {}
  }
  if (item.state === "heard") return "后来听闻";
  return item.source || "亲历";
}

async function sendTurn(continuation, presetContent = "") {
  const conversationId = state.conversation?.conversation.id;
  if (!conversationId || worldBusy() || pendingSettlements().length || state.recovering || state.conversation.needs_scene_entry) return;
  const input = $("#composer-input");
  const content = continuation ? "" : (presetContent || input.value.trim());
  if (!continuation && !content) return;
  const worldId = state.world.world.id;
  const live = {messages:new Map(), records:[], settlements:[], phase:"speaking"};
  state.liveTurns.set(conversationId, live);
  state.streamingConversationIds.add(conversationId);
  state.writingConversationIds.delete(conversationId);
  setActivity(worldId, "角色正在回应");
  let acknowledged = continuation, assistantId = null, frame = 0;
  if (!continuation) { state.conversationDrafts.delete(conversationId); input.value = ""; }
  // Sending follows the newest text; subsequent updates respect manual scrolling.
  $("#messages").scrollTop = $("#messages").scrollHeight;
  renderChat();
  const isCurrent = () => state.conversation?.conversation.id === conversationId;
  const repaint = () => { if (isCurrent()) renderChat(); };
  const flush = () => {
    frame = 0;
    if (!isCurrent() || !assistantId) return;
    const node = $(`[data-message-id="${assistantId}"]`, $("#messages"));
    if (!node) return;
    const scroller = $("#messages");
    const follow = isNearMessagesEnd(scroller);
    $(".reply-waiting", node)?.remove();
    $(".message-body", node).textContent = live.messages.get(assistantId).content;
    $(".message-body", node).classList.add("typing-caret");
    if (follow) scroller.scrollTop = scroller.scrollHeight;
    updateLatestButton($("#chat-view"));
  };
  try {
    const response = await fetch(`/api/conversations/${conversationId}/turn`, {
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({content, continuation}),
    });
    if (!response.ok || !response.body) throw new Error("没有接到世界的回应");
    await readStream(response, event => {
      if (event.type === "user") { acknowledged = true; live.messages.set(event.message.id, event.message); repaint(); }
      if (event.type === "assistant_start") { assistantId = event.message.id; live.messages.set(assistantId, event.message); repaint(); }
      if (event.type === "delta" && assistantId) {
        live.messages.get(assistantId).content = event.full;
        if (!frame) frame = requestAnimationFrame(flush);
      }
      if (event.type === "assistant_complete" && assistantId) {
        if (frame) cancelAnimationFrame(frame); flush();
        Object.assign(live.messages.get(assistantId), {status:"complete", writing:true, settlement_job:null});
        live.phase = "writing";
        state.writingConversationIds.add(conversationId);
        setActivity(worldId, "世界正在留下痕迹"); repaint();
      }
      if (event.type === "world_patch" && assistantId) {
        const message = live.messages.get(assistantId); message.writing = false;
        if (event.result.record) live.records.push({...event.result.record, source_message_id:assistantId});
        if (event.result.settlement) live.settlements.push(event.result.settlement);
        repaint();
      }
      if (event.type === "patch_skipped" && assistantId) {
        Object.assign(live.messages.get(assistantId), {writing:false, settlement_job:{status:"failed", reason:event.reason}});
        repaint();
      }
      if (event.type === "error") throw new Error(event.error.message);
    });
  } catch (error) {
    if (!acknowledged) state.conversationDrafts.set(conversationId, content);
    toast(error.message);
  } finally {
    if (frame) cancelAnimationFrame(frame);
    live.messages.forEach(message => { message.writing = false; if (message.status === "streaming") message.status = "error"; });
    try {
      const data = await api(`/api/conversations/${conversationId}`);
      state.liveTurns.delete(conversationId);
      if (isCurrent()) state.conversation = data;
    } catch { /* Keep the last visible snapshot until the next successful read. */ }
    await refreshWorld(worldId).catch(() => {});
    state.streamingConversationIds.delete(conversationId);
    state.writingConversationIds.delete(conversationId);
    setActivity(worldId);
    renderCurrent();
  }
}

function isNearMessagesEnd(node) { return node.scrollHeight - node.scrollTop - node.clientHeight < 120; }

function showView(view) {
  state.view = view;
  const root = $(`#${view}-view`);
  if (!reducedMotion.matches) root.animate([{opacity:.4, transform:"translateY(5px)"}, {opacity:1, transform:"none"}], {duration:220, easing:"cubic-bezier(.2,.8,.2,1)"});
  $("#world-view").classList.toggle("active", view === "world");
  $("#chat-view").classList.toggle("active", view === "chat");
  $("#room-view").classList.toggle("active", view === "room");
  $(".nav-button").classList.toggle("active", view === "world");
  $("#mobile-back").classList.toggle("visible", view === "chat" || view === "room");
  setInspector(false);
  updateHeader();
}

function showWorldHome() {
  if (!state.world) return;
  rememberCurrentView(); ++state.navigation;
  document.body.classList.remove("navigating");
  clearTimeout(openWorld.introTimer); state.playingWorldIntro = false;
  state.character = null; state.conversation = null; state.room = null;
  showView("world"); renderShell(); renderWorld(); renderWorldInspector();
  refreshWorld().catch(error => toast(error.message));
}

function updateHeader() {
  if (!state.world) return;
  const title = state.view === "chat" && state.character ? state.character.name
    : state.view === "room" && state.room ? state.room.room.name
    : state.world.world.name;
  $("#stage-title").textContent = title;
  $("#stage-eyebrow").textContent = state.view === "chat" ? "私人现场" : state.view === "room" ? "同场" : "共享世界";
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

function openSettings() { fillProviderForm(); $("#provider-feedback").textContent = ""; showDialog("#provider-dialog"); }
function closeSettings() { closeDialog("#provider-dialog"); }

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
  if (state.screen === "world" && (worldBusy() || pendingSettlements().length)) { toast("还有未记下的余波，请稍候"); return; }
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
  showDialog("#world-dialog");
}

function closeWorldEditor() { closeDialog("#world-dialog"); }

function openCharacterEditor(isNew = false) {
  if (state.screen === "world" && (worldBusy() || pendingSettlements().length)) { toast("还有未记下的余波，请稍候"); return; }
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
  refreshDesireHints();
  showDialog("#character-dialog");
}

function closeCharacterEditor() { closeDialog("#character-dialog"); }

function openResetWorld() {
  if (!state.world || state.resettingWorld) return;
  const name = state.world.world.name;
  const form = $("#reset-world-form");
  $("#reset-world-name").textContent = name;
  $("#reset-world-feedback").textContent = "";
  $("#reset-world-feedback").className = "form-feedback";
  form.elements.confirmation.value = "";
  $("#confirm-reset-world").disabled = true;
  showDialog("#reset-world-dialog");
  requestAnimationFrame(() => form.elements.confirmation.focus());
}

function closeResetWorld() {
  if (!state.resettingWorld) closeDialog("#reset-world-dialog");
}

$("#reset-world-dialog").oncancel = event => {
  if (state.resettingWorld) event.preventDefault();
};

$("#reset-world-form").elements.confirmation.oninput = event => {
  $("#confirm-reset-world").disabled = event.target.value.trim() !== state.world?.world.name;
};

$("#reset-world-form").onsubmit = async event => {
  event.preventDefault();
  if (!state.world || state.resettingWorld) return;
  const form = event.currentTarget;
  const worldId = state.world.world.id;
  const confirmation = form.elements.confirmation.value.trim();
  if (confirmation !== state.world.world.name) return;
  state.resettingWorld = true;
  const button = $("#confirm-reset-world");
  const feedback = $("#reset-world-feedback");
  button.disabled = true;
  button.textContent = "正在重置…";
  feedback.textContent = "世界正在退回开场";
  try {
    const result = await api(`/api/worlds/${worldId}/reset`, {
      method: "POST", body: JSON.stringify({confirmation}),
    });
    $("#reset-world-dialog").close();
    state.conversation = null;
    state.character = null;
    state.room = null;
    state.rooms = [];
    state.roomStreaming = false;
    state.roomWriting = false;
    state.viewMemory.clear(); state.advanceCustom.clear(); state.advanceChoices.clear();
    state.conversationDrafts.clear();
    state.roomDrafts.clear();
    state.streamingConversationIds.clear();
    state.writingConversationIds.clear();
    state.openingConversationIds.clear();
    await refreshBootstrap();
    await openWorld(worldId);
    const cleared = result.cleared || {};
    toast(`世界已回到开场 · 清除了 ${cleared.messages || 0} 条对话`);
  } catch (error) {
    feedback.className = "form-feedback bad";
    feedback.textContent = error.message;
  } finally {
    state.resettingWorld = false;
    button.textContent = "重置世界";
    button.disabled = form.elements.confirmation.value.trim() !== state.world?.world.name;
  }
};

$("#provider-protocol").onchange = event => {
  const form = $("#provider-form");
  if (event.target.value === "anthropic-messages") { form.elements.base_url.value = "https://api.anthropic.com"; form.elements.path.value = "/v1/messages"; }
  else { form.elements.base_url.value = "https://api.openai.com"; form.elements.path.value = "/v1/chat/completions"; }
};

$("#provider-form").onsubmit = async event => {
  event.preventDefault();
  const submitForm = event.currentTarget;
  await formAction(submitForm, submitForm.querySelector('[type="submit"]'), "正在保存…", async () => {
  const body = Object.fromEntries(new FormData(submitForm));
  body.max_tokens = Number(body.max_tokens); body.temperature = Number(body.temperature); body.id = state.provider?.id || "provider_default";
  try {
    state.provider = await api("/api/provider", {method:"PUT", body:JSON.stringify(body)});
    renderProviderState();
    $("#provider-feedback").className = "form-feedback good";
    $("#provider-feedback").textContent = "已保存";
    closeSettings();
    toast("模型连接已保存");
  } catch (error) { $("#provider-feedback").className = "form-feedback bad"; $("#provider-feedback").textContent = error.message; }
  });
};

$("#test-provider").onclick = async () => {
  await formAction($("#provider-form"), $("#test-provider"), "正在连接…", async () => {
  const feedback = $("#provider-feedback");
  feedback.className = "form-feedback"; feedback.textContent = "正在连接…";
  try {
    const form = $("#provider-form");
    const body = Object.fromEntries(new FormData(form)); body.max_tokens = Number(body.max_tokens); body.temperature = Number(body.temperature); body.id = state.provider?.id || "provider_default";
    state.provider = await api("/api/provider", {method:"PUT", body:JSON.stringify(body)});
    const result = await api("/api/provider/test", {method:"POST", body:"{}"});
    feedback.className = "form-feedback good"; feedback.textContent = `${result.model} · ${result.reply || "连接成功"}`; renderProviderState();
  } catch (error) { feedback.className = "form-feedback bad"; feedback.textContent = error.message; }
  });
};

document.addEventListener("click", event => {
  const template = event.target.closest("[data-template]")?.dataset.template;
  if (template !== undefined) selectTemplate(template);
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "retry-settlements") retrySettlements();
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
  if (action === "reset-world") openResetWorld();
  if (action === "close-reset-world") closeResetWorld();
  if (action === "new-room") openRoomDialog();
  if (action === "close-room-dialog") closeRoomDialog();
  if (action === "close-room") showWorldHome();
  if (action === "toggle-inspector") setInspector(!$("#inspector").classList.contains("open"));
  if (action === "close-inspector") setInspector(false);
  if (action === "latest") {
    const root = event.target.closest(".view");
    const scroller = $(".messages", root);
    scroller.scrollTo({top:scroller.scrollHeight, behavior:reducedMotion.matches ? "instant" : "smooth"});
  }
});

$("#room-form").onsubmit = async event => {
  event.preventDefault();
  const submitForm = event.currentTarget;
  await formAction(submitForm, submitForm.querySelector('[type="submit"]'), "正在开启…", async () => {
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
  });
};

$("#world-form").onsubmit = async event => {
  event.preventDefault();
  const submitForm = event.currentTarget;
  await formAction(submitForm, submitForm.querySelector('[type="submit"]'), "正在保存…", async () => {
  const body = Object.fromEntries(new FormData(submitForm));
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
  });
};

$("#add-location").onclick = async () => {
  const name = $("#new-location-name").value.trim();
  if (!name || !state.editingWorldId) return;
  await formAction($("#world-form"), $("#add-location"), "正在加入…", async () => {
  try {
    await api(`/api/worlds/${state.editingWorldId}/locations`, {method:"POST", body:JSON.stringify({name,mood:$("#new-location-mood").value.trim(),description:$("#new-location-description").value.trim()})});
    await refreshWorld();
    $("#editor-location-list").innerHTML = state.world.locations.map(item => `<span>${escapeHTML(item.name)}</span>`).join("");
    $("#new-location-name").value = ""; $("#new-location-mood").value = ""; $("#new-location-description").value = "";
    renderWorldInspector(); toast("地点已加入");
  } catch (error) { toast(error.message); }
  });
};

$("#character-form").onsubmit = async event => {
  event.preventDefault();
  const submitForm = event.currentTarget;
  await formAction(submitForm, submitForm.querySelector('[type="submit"]'), "正在保存…", async () => {
  const raw = Object.fromEntries(new FormData(submitForm));
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
    if (editingId) { await reloadConversation(); toast("人物已保存"); } else { showWorldHome(); toast("人物已加入这个世界"); }
  } catch (error) { toast(error.message); }
  });
};

$$("#character-form .desire-editor input[type=range]").forEach(input => input.addEventListener("input", refreshDesireHints));

document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !$("dialog[open]")) setInspector(false);
  if (event.key === "Tab" && $("#inspector.open") && !$("dialog[open]")) {
    const controls = $$("button:not(:disabled), [href], input, select, textarea", $("#inspector"));
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});
for (const dialog of $$("dialog")) {
  dialog.addEventListener("cancel", event => {
    event.preventDefault();
    if (dialog.dataset.busy !== "true" && !state.resettingWorld) closeDialog(`#${dialog.id}`);
  });
  dialog.addEventListener("click", event => {
    const rect = dialog.getBoundingClientRect();
    const outside = event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
    if (event.target === dialog && outside && dialog.dataset.busy !== "true" && !state.resettingWorld) closeDialog(`#${dialog.id}`);
  });
}

window.addEventListener("resize", () => { if (window.innerWidth > 1200) setInspector(false); });
bootstrap().catch(error => toast(error.message));
