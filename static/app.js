const state = {
  spaceId: null, worlds: [], world: null, provider: null, templates: [],
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
  worldActivities: new Map(), liveTurns: new Map(), liveRooms: new Map(),
  viewMemory: new Map(),
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
  if (label && !worldBusy(worldId)) invalidateTimePreview(worldId);
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
  scroller.scrollTop = memory && !memory.end ? memory.top : (scroller === root ? memory?.top || 0 : scroller.scrollHeight);
  requestAnimationFrame(() => {
    if (root.dataset.viewKey !== key) return;
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

function appPath(path) {
  return new URL(path.replace(/^\//, ""), window.location.href).href;
}

async function appFetch(path, options = {}) {
  const headers = {...(options.headers || {})};
  if (state.spaceId) headers["X-Gongjie-Space"] = state.spaceId;
  const response = await fetch(appPath(path), {...options, headers});
  if (response.status === 401 || response.headers?.get("X-Gongjie-Session") === "changed") {
    window.location.replace(appPath("/"));
    throw new Error("存档登录状态已变化，正在重新载入");
  }
  return response;
}

async function api(path, options = {}) {
  const response = await appFetch(path, {...options, headers:{"Content-Type":"application/json", ...(options.headers || {})}});
  if (!response.ok) {
    let detail = "请求失败";
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : (Array.isArray(body.detail) ? body.detail.map(item => {
        const label = ({name:"名称",age:"年龄",protocol:"连接方式",max_tokens:"回复长度",temperature:"温度",target_time:"目标时刻"})[item.loc?.[1]] || "输入内容";
        if (item.type === "string_too_short") return `${label}不能为空`;
        if (item.type === "greater_than_equal") return `${label}不能小于${item.ctx.ge}`;
        if (item.type === "less_than_equal") return `${label}不能大于${item.ctx.le}`;
        return `${label}填写有误，请检查`;
      }).join("；") : body.detail?.message || detail);
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

const CHARACTER_ART = {
  daylight_lorne: {file:"lorne", x:50, y:8},
  daylight_severin: {file:"severin", x:50, y:2},
  daylight_evelyn: {file:"evelyn", x:45, y:4},
  daylight_vesper: {file:"vesper", x:51, y:0},
};
function characterArt(character) {
  const art = CHARACTER_ART[character.art_key];
  return art ? {...art, url:appPath(`/static/characters/daylight/${art.file}.jpg`)} : null;
}
function avatar(character, size = "") {
  const accent = /^#[0-9a-f]{6}$/i.test(character.accent || "") ? character.accent : "#bc5d45";
  const art = characterArt(character);
  return `<span class="avatar ${size} ${art ? "illustrated" : ""}" style="--accent:${accent};${art ? `--face-x:${art.x}%;--face-y:${art.y}%` : ""}">${art ? `<img src="${art.url}" alt="" loading="lazy" decoding="async">` : escapeHTML(character.name?.slice(0,1) || "·")}</span>`;
}
function openCharacterPortrait(characterId) {
  const character = state.world?.characters.find(item => item.id === characterId);
  if (!character) return;
  const art = characterArt(character);
  const dialog = $("#portrait-dialog");
  $("#portrait-title").textContent = character.name;
  $("#portrait-role").textContent = `${character.role} · ${character.age} 岁`;
  $("#portrait-tagline").textContent = character.tagline || "";
  $("#portrait-art").innerHTML = art ? `<img src="${art.url}" alt="${escapeHTML(character.name)}的完整立绘">` : avatar(character, "large");
  dialog.classList.toggle("has-art", Boolean(art));
  const enter = $("#portrait-enter");
  enter.textContent = effectiveAvailability(character) === "unavailable" ? "暂时无法相遇" : `去见${character.pronouns === "她" ? "她" : "他"}`;
  enter.disabled = effectiveAvailability(character) === "unavailable" || worldBusy() || Boolean(pendingSettlements().length);
  enter.onclick = () => { closeDialog("#portrait-dialog"); openCharacter(character.id); };
  showDialog("#portrait-dialog");
}

function relationshipFor(characterId) {
  return state.world?.relationships.find(item => item.from_character_id === characterId && item.to_user) || {};
}

async function bootstrap() {
  const data = await api("/api/bootstrap");
  state.worlds = data.worlds;
  state.provider = data.provider;
  state.templates = data.templates;
  state.spaceId = data.space_id;
  $("#space-controls").hidden = !data.access_enabled;
  $("#space-label").textContent = data.space_label || "";
  renderProviderState();
  renderLibrary();
  showLibrary(false);
  fillProviderForm();
}

async function refreshBootstrap() {
  const data = await api("/api/bootstrap");
  state.worlds = data.worlds;
  state.provider = data.provider;
  state.templates = data.templates;
  state.spaceId = data.space_id;
  $("#space-controls").hidden = !data.access_enabled;
  $("#space-label").textContent = data.space_label || "";
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
  ++state.worldRefresh;
  invalidateTimePreview(worldId);
  try {
    const [world, roomsData] = await Promise.all([
      api(`/api/worlds/${worldId}`), api(`/api/worlds/${worldId}/rooms`),
    ]);
    if (token !== state.navigation) return;
    state.world = world; state.rooms = roomsData.rooms || []; previewErrors.delete(worldId);
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
  invalidateTimePreview(worldId);
  const revision = ++state.worldRefresh;
  const [world, roomsData] = await Promise.all([
    api(`/api/worlds/${worldId}`), api(`/api/worlds/${worldId}/rooms`),
  ]);
  if (state.world?.world.id !== worldId || revision !== state.worldRefresh) return;
  state.world = world; state.rooms = roomsData.rooms || []; previewErrors.delete(worldId);
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
  return [...new Map([...(state.world?.pending_settlements || []), ...(data?.pending_settlements || [])].map(job => [job.source_id || job.id, job])).values()];
}

function settlementRecoveryHTML(job = null) {
  if (!job && !pendingSettlements().length) return "";
  const first = pendingSettlements()[0] || job;
  if (first?.status === "pending") {
    watchSettlementProgress();
    return `<div class="settlement-recovery" role="status"><span>世界正在留下痕迹</span></div>`;
  }
  return `<div class="settlement-recovery" role="status"><span>${state.recovering ? "世界正在留下痕迹" : "还有未记下的余波"}</span><button class="soft-button" data-action="retry-settlements" ${state.recovering || worldBusy() ? "disabled" : ""}>${state.recovering ? "正在记录…" : "重新记录"}</button></div>`;
}

function watchSettlementProgress() {
  if (state.settlementTimer || worldBusy() || state.recovering) return;
  const worldId = state.world?.world.id;
  state.settlementTimer = setTimeout(async () => {
    state.settlementTimer = null;
    if (state.world?.world.id !== worldId || worldBusy() || state.recovering) return;
    try {
      await refreshWorld(worldId);
      if (state.world?.world.id !== worldId) return;
      if (state.conversation) await reloadConversation();
      if (state.room) {
        const roomId = state.room.room.id;
        const data = await api(`/api/rooms/${roomId}`);
        if (state.room?.room.id === roomId) state.room = data;
      }
    } catch { /* A later refresh can resume observing the persisted task. */ }
    renderCurrent();
  }, 3000);
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
  const hasProgress = characters.some(character => character.has_messages);
  const heroLabel = pulse.narration ? "本时段开场" : hasProgress ? "世界背景" : "故事开场";
  const heroText = pulse.narration || (hasProgress ? world.premise : world.opening_narration || world.premise) || "这一刻还没有落笔。";
  let cardIndex = 0;
  const encounterSections = encounterGroups.map(([key, title]) => {
    const members = characters.filter(character => effectiveAvailability(character, player) === key);
    if (!members.length) return "";
    const cards = members.map(character => characterCard(character, cardIndex++, player)).join("");
    return `<div class="encounter-group"><div class="encounter-group-head">${title}</div><div class="world-character-grid">${cards}</div></div>`;
  }).join("");
  $("#world-view").innerHTML = `<div class="world-wrap ${state.playingWorldIntro ? "entering" : ""}">
    <article class="world-hero">
      <div class="world-hero-copy"><span class="stage-eyebrow">${heroLabel}</span><h2>${escapeHTML(world.current_time || world.name)}</h2><p>${escapeHTML(heroText)}</p></div>
      <div class="world-hero-time"><small>${escapeHTML(world.atmosphere || "共享世界")}</small><strong>${escapeHTML(world.name)}</strong></div>
      <div class="world-gate" aria-hidden="true"><i></i><i></i><i></i><span></span></div>
    </article>

    ${settlementRecoveryHTML()}
    <section class="player-presence world-secondary">
      <div class="player-presence-mark"><i></i><span>你</span></div>
      <div class="player-presence-main"><span class="stage-eyebrow">此刻的你</span><h3>${escapeHTML(player.location_name || "尚未走进某个现场")}</h3><p>${escapeHTML(player.condition || "还没有留下足以被旁人察觉的变化。")}</p></div>
      <button class="soft-button" data-action="edit-player">我的角色</button>
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
      <div><span class="stage-eyebrow">WORLD PULSE</span><h3>${state.pulsing ? "所有人都在决定下一步" : pending ? `${pending} 件余波正等着世界回应` : "这一刻还可以继续，也可以向前"}</h3>${timePreviewHTML()}</div>
      <button id="pulse-world" class="primary-button" ${!state.world.time_preview || worldBusy() || pendingSettlements().length || state.recovering ? "disabled" : ""}>${state.pulsing ? "世界正在向前…" : "让这一刻继续"}</button>
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
  ensureTimePreview();
}

function promiseHTML(item) {
  const character = state.world.characters.find(person => person.id === item.character_id);
  const available = item.ready_to_visit && character && effectiveAvailability(character) !== "unavailable";
  return `<span class="player-promise"><span>${escapeHTML(item.text)}<small class="promise-time">${escapeHTML(state.world.time_preview ? (state.world.time_preview.appointment_times?.[item.id] || "时间未定") : "约定时间待确认")} · ${item.ready_to_visit ? "可赴约" : "待赴约"}</small></span>${available
    ? `<button data-world-character="${character.id}">前往赴约 ↗</button>`
    : item.character_name ? `<small>${escapeHTML(item.character_name)}</small>` : ""}</span>`;
}

function effectiveAvailability(character, player = state.world?.player || {}) {
  if (character.availability === "unavailable") return "unavailable";
  if (player.location_id && character.current_location_id === player.location_id) return "present";
  return character.availability === "present" ? "reachable" : character.availability || "reachable";
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
  invalidateTimePreview(worldId);
  try {
    const room = await api(`/api/rooms/${roomId}/enter`, {method:"POST", body:"{}"});
    const world = await api(`/api/worlds/${worldId}`);
    if (token !== state.navigation || state.world?.world.id !== worldId) return;
    if (!worldBusy(worldId)) state.liveRooms.delete(roomId);
    state.room = room; state.world = world;
    state.conversation = null; state.character = null; state.view = "room";
    renderShell(); renderRoom(); renderRoomInspector(); showView("room");
  } catch (error) { if (token === state.navigation) toast(error.message); }
  finally { finishNavigation(token); }
}

function renderRoom() {
  rememberView($("#room-view"));
  const data = mergeLive(state.room, state.liveRooms.get(state.room.room.id));
  const room = data.room;
  const inputLocked = state.liveRooms.has(room.id) && !state.liveRooms.get(room.id).finished && !state.liveRooms.get(room.id).drafting;
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
    ${state.liveRooms.has(room.id) && !state.liveRooms.get(room.id).finished ? '<div class="room-waiting"><span class="reply-waiting"><i></i><i></i><i></i></span> 同场正在继续</div>' : ""}
    <button class="latest-button" data-action="latest">回到最新 ↓</button>
    ${data.messages.some(message => message.settlement_job) ? "" : settlementRecoveryHTML()}
    <div class="composer-shell"><div class="composer">
      <textarea id="room-composer-input" placeholder="对在场的人说……" rows="2" ${inputLocked ? "disabled" : ""}>${escapeHTML(draft)}</textarea>
      <div class="composer-actions"><button id="room-send-turn" class="send-button" aria-label="发送" ${turnLocked ? "disabled" : ""}>↑</button></div>
    </div></div>`;
  $("#room-send-turn").onclick = () => sendRoomTurn();
  bindComposer($("#room-composer-input"), state.roomDrafts, room.id, sendRoomTurn);
  restoreView($("#room-view"), viewKey("room"));
  ensureTimePreview();
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
  $("#inspector").insertAdjacentHTML("afterbegin", '<button id="close-inspector" class="inspector-close icon-button" data-action="close-inspector" aria-label="关闭视角">×</button><button class="soft-button mobile-save-entry" data-action="saves">存档 / 读档</button>');
}

async function sendRoomTurn() {
  if (!state.room || worldBusy() || pendingSettlements().length || state.recovering) return;
  const input = $("#room-composer-input");
  const content = input.value.trim();
  if (!content) return;
  const roomId = state.room.room.id, worldId = state.world.world.id;
  const characters = state.room.characters;
  const live = {messages:new Map(), records:[], settlements:[], phase:"responding"};
  live.messages.set("pending-user", {id:"pending-user", role:"user", content, status:"pending"});
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
    const response = await appFetch(`/api/rooms/${roomId}/turn`, {
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({content, continuation:false}),
    });
    if (!response.ok || !response.body) throw new Error("没有接到房间的回应");
    await readStream(response, event => {
      if (event.type === "user") {
        acknowledged = true;
        const waiting = live.messages.get("pending-assistant");
        live.messages.delete("pending-user"); live.messages.delete("pending-assistant");
        live.messages.set(event.message.id, event.message);
        if (waiting) live.messages.set("pending-assistant", waiting);
        repaint();
      }
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
    if (!acknowledged) { state.roomDrafts.set(roomId, content); live.messages.delete("pending-user"); }
    toast(error.message);
  } finally {
    live.finished = true;
    live.messages.forEach(message => { message.writing = false; });
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
  const hasCurrentScene = character.has_messages || Number(character.pulse_number || 0) > 0;
  const pulseHook = hasCurrentScene && character.encounter_hook !== character.opening_hook
    ? character.encounter_hook : "";
  const summary = pulseHook || (hasCurrentScene
    ? (character.state_summary || character.tagline || "还没有留下此刻的姿态。")
    : (character.opening_hook || character.tagline || "此刻可以去见他。"));
  const unavailable = availability === "unavailable";
  return `<button class="world-character-card ${availability}" style="--card-index:${index}" data-portrait="${character.id}">
    <div class="encounter-state">${escapeHTML(availabilityText)}</div>
    <div class="character-card-head">${avatar(character, "large")}<div><h4>${escapeHTML(character.name)}</h4><small>${escapeHTML(character.role)} · ${escapeHTML(character.location_name || "地点未定")}</small></div><span>查看 ›</span></div>
    <p>${escapeHTML(summary)}</p>
    <div class="relationship-line"><span>${escapeHTML(character.has_messages && relationship.notes ? "与你的近况" : relationship.label || "关系尚未命名")}</span><i></i><strong>${escapeHTML(relationship.notes || "还没有留下共同经历")}</strong></div>
  </button>`;
}

function renderWorldInspector() {
  const {world, characters, locations, events, pending_sync_count} = state.world;
  const player = state.world.player || {};
  $("#inspector").innerHTML = `<div class="world-inspector">
    <div class="inspector-title">世界介绍</div>
    <h2>${escapeHTML(world.name)}</h2><p>${escapeHTML(world.premise || "")}</p>
    <div class="inspector-actions"><button class="soft-button" data-action="edit-world">编辑世界书</button><button class="soft-button" data-action="new-character">新人物</button></div>
    <div class="world-counts"><span><b>${characters.length}</b>人物</span><span><b>${locations.length}</b>地点</span><span><b>${events.length}</b>公开大事</span></div>
    <article class="inspector-card"><h4>此刻的你</h4><p>${escapeHTML(player.location_name || "位置尚未确定")}<br>${escapeHTML(player.condition || "没有明显的外部变化")}<br>${state.world.pending_settlements?.length ? "还有未记下的余波。" : pending_sync_count ? `${pending_sync_count} 件变化会在世界向前时传播。` : "此刻的余波已经记下。"}</p></article>
    <button class="reset-world-button" data-action="reset-world">重置这个世界</button>
  </div>`;
  $("#inspector").insertAdjacentHTML("afterbegin", '<button id="close-inspector" class="inspector-close icon-button" data-action="close-inspector" aria-label="关闭视角">×</button><button class="soft-button mobile-save-entry" data-action="saves">存档 / 读档</button>');
}

const previewRequests = new Map();
const previewErrors = new Map();
const previewRevisions = new Map();
function invalidateTimePreview(worldId) {
  previewRevisions.set(worldId, (previewRevisions.get(worldId) || 0) + 1);
  previewErrors.delete(worldId);
  if (state.world?.world.id === worldId) state.world.time_preview = null;
}
function timePreviewHTML() {
  const preview = state.world?.time_preview;
  if (preview) return `<p class="time-preview">下一时刻 · ${escapeHTML(preview.world_time)}</p>`;
  if (previewErrors.has(state.world?.world.id)) return '<p class="time-preview">暂未确定下一时刻 <button class="text-button" data-action="retry-preview">重试</button></p>';
  if (worldBusy() || pendingSettlements().length || (state.view === "chat" && state.conversation?.needs_scene_entry)) return "";
  return '<p class="time-preview">正在确定下一时刻…</p>';
}
function renderTimePreview() {
  if (state.view === "world") { renderCurrent(); return; }
  const root = $(`#${state.view}-view`);
  const advance = $("#advance-turn", root);
  if (advance) advance.disabled = !state.world.time_preview || worldBusy() || pendingSettlements().length || state.recovering || state.conversation?.needs_scene_entry || !state.conversation?.messages.length;
  ensureTimePreview();
}
async function ensureTimePreview() {
  const worldId = state.world?.world.id;
  if (!worldId || state.screen === "library" || state.world.time_preview || worldBusy() || pendingSettlements().length || (state.view === "chat" && state.conversation?.needs_scene_entry) || previewRequests.has(worldId) || previewErrors.has(worldId)) return;
  const revision = previewRevisions.get(worldId) || 0;
  const isCurrent = () => state.world?.world.id === worldId && (previewRevisions.get(worldId) || 0) === revision;
  previewRequests.set(worldId, true);
  try {
    const preview = await api(`/api/worlds/${worldId}/time-preview`, {method:"POST",body:"{}"});
    // Fetch current state as well: a turn may have changed it while planning.
    const fresh = await api(`/api/worlds/${worldId}`);
    if (isCurrent()) {
      state.world.time_preview = fresh.time_preview?.id === preview.id ? preview : null;
      if (!state.world.time_preview) previewErrors.set(worldId, "时间预告已过期");
    }
  } catch (error) { if (isCurrent()) previewErrors.set(worldId,error.message); }
  finally { previewRequests.delete(worldId); if (state.world?.world.id === worldId) renderTimePreview(); }
}

async function openSaves() {
  if (!state.world) return;
  const worldId = state.world.world.id;
  const saves = await api(`/api/worlds/${worldId}/saves`);
  if (state.world?.world.id !== worldId) return;
  const dialog = $("#saves-dialog");
  const perform = async action => {
    if (dialog.dataset.busy === "true") return;
    if (worldBusy(worldId) || pendingSettlements().length) { toast("请等当前回合记录完成"); return; }
    dialog.dataset.busy = "true";
    $$("button", dialog).forEach(button => button.disabled = true);
    setActivity(worldId, "正在整理存档");
    try { await action(); }
    catch (error) { toast(error.message); }
    finally {
      dialog.dataset.busy = "false";
      $$("button", dialog).forEach(button => button.disabled = false);
      setActivity(worldId); renderCurrent();
      if (dialog.dataset.closeAfterBusy === "true") { delete dialog.dataset.closeAfterBusy; closeDialog("#saves-dialog"); }
    }
  };
  $("#save-list").innerHTML = saves.length ? saves.map(save => `<article class="save-slot"><div><strong>${escapeHTML(save.name)}</strong><p>${escapeHTML(save.world_time)}</p><small>${escapeHTML(new Date(save.created_at).toLocaleString())}</small></div><button class="soft-button" data-load-save="${escapeHTML(save.id)}">读取</button></article>`).join("") : '<p class="save-empty">还没有存档</p>';
  $("#create-save").onclick = () => perform(async () => {
    await api(`/api/worlds/${worldId}/saves`, {method:"POST",body:"{}"}); await openSaves(); toast("进度已保存");
  });
  $("#save-list").onclick = async event => {
    const button = event.target.closest("[data-load-save]");
    if (!button) return;
    await perform(async () => {
      await api(`/api/worlds/${worldId}/saves/${button.dataset.loadSave}/load`, {method:"POST",body:"{}"});
      closeDialog("#saves-dialog");
      state.liveTurns.clear(); state.liveRooms.clear(); state.viewMemory.clear();
      state.conversationDrafts.clear();
      state.roomDrafts.clear();
      await openWorld(worldId); toast("已读档，原进度已自动备份");
    });
  };
  if (!$("#saves-dialog").open) showDialog("#saves-dialog");
}

async function advanceWorld() { await runWorldPulse(); }

function renderCurrent() {
  if (!state.world) return;
  if (state.view === "chat" && state.conversation) { renderChat(); renderCharacterInspector(); }
  else if (state.view === "room" && state.room) { renderRoom(); renderRoomInspector(); }
  else { renderWorld(); renderWorldInspector(); }
}

async function runWorldPulse() {
  if (!state.world || !state.world.time_preview || worldBusy() || pendingSettlements().length) return;
  const worldId = state.world.world.id, token = state.navigation, previewId = state.world.time_preview.id;
  state.pulsing = true; setActivity(worldId, "世界正在向前"); renderCurrent();
  try {
    const result = await api(`/api/worlds/${worldId}/pulse`, {method:"POST", body:JSON.stringify({preview_id:previewId})});
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
  } catch (error) {
    toast(error.message);
    await refreshWorld(worldId).catch(() => {});
  }
  finally { state.pulsing = false; setActivity(worldId); renderCurrent(); }
}


async function openCharacter(characterId) {
  if (state.view === "chat" && state.character?.id === characterId) return;
  const worldId = state.world.world.id;
  const token = beginNavigation();
  invalidateTimePreview(worldId);
  try {
    const data = await api("/api/conversations", {method:"POST", body:JSON.stringify({world_id:worldId, character_id:characterId})});
    const world = await api(`/api/worlds/${worldId}`);
    if (token !== state.navigation || state.world?.world.id !== worldId) return;
    const conversationId = data.conversation.id;
    if (!state.streamingConversationIds.has(conversationId)) state.liveTurns.delete(conversationId);
    state.conversation = data; state.character = data.character; state.room = null;
    state.world = world; state.view = "chat";
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
  const navigation = state.navigation;
  const data = await api(`/api/conversations/${conversationId}`);
  if (state.conversation?.conversation.id !== conversationId || navigation !== state.navigation) return;
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
      <button class="chat-person portrait-trigger" data-portrait="${character.id}" aria-label="查看${escapeHTML(character.name)}的角色卡">${avatar(character)}<div><strong>${escapeHTML(character.name)}</strong><small>${escapeHTML(character.location_name || character.role)}</small></div></button>
      <button class="soft-button" data-action="edit-player">我的角色</button><span class="live-world-badge">世界持续记录</span>
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
          <button id="advance-turn" class="continue-button" title="跳过一段时间，让整个世界向前" ${state.world.time_preview && data.messages.length && !turnLocked && !state.pulsing ? "" : "disabled"}>让世界向前</button>
        </div>
        <button id="send-turn" class="send-button" aria-label="发送" ${turnLocked ? "disabled" : ""}>↑</button>
      </div>
  </div></div>`;
  const input = $("#composer-input");
  $("#send-turn").onclick = () => sendTurn(false);
  $("#continue-turn").onclick = () => sendTurn(true);
  $("#advance-turn").onclick = () => advanceWorld();
  $("#retry-opening")?.addEventListener("click", () => generateOpening());
  bindComposer(input, state.conversationDrafts, conversationId, () => sendTurn(false));
  restoreView($("#chat-view"), viewKey("chat"));
  ensureTimePreview();
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
  const evidence = settlement.evidence || {};
  const rows = [
    [evidence["scene.location"] || evidence["player.location"], "地点", scene.location || player.location],
    [evidence["scene.world_time"], "时间", scene.world_time],
    [evidence.relationship, "关系", [relationship.label, relationship.note,
      ...[["trust_delta","信任"],["desire_delta","欲望"],["tension_delta","张力"]].filter(([key]) => relationship[key]).map(([key,label]) => `${label} ${relationship[key] > 0 ? "+" : ""}${relationship[key]}`)].filter(Boolean).join(" · ")],
    [evidence["player.commitments"], "仍要兑现", readableList(player.commitments) || "已无待兑现的承诺"],
  ].filter(([changed,, value]) => changed && String(value || "").trim()).map(([,label,value]) => [label,value]);
  if (!rows.length) return "";
  return `<details class="settlement-fields"><summary>这一轮留下的变化</summary><ul>${rows.map(([label, value]) => `<li><span>${escapeHTML(label)}</span><p>${escapeHTML(value)}</p></li>`).join("")}</ul></details>`;
}

function recordHTML(record = null, settlement = null) {
  if (settlement?.decision?.outcome === "rejected") return `<aside class="world-record"><small>正文已保留 · 本轮变化未被确认</small></aside>`;
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
    <div class="inspector-actions"><button class="soft-button" data-portrait="${character.id}">角色立绘</button><button class="soft-button" data-action="edit-character">编辑人物</button></div>
    <article class="inspector-card"><h4>当前现场</h4><p>${escapeHTML(sceneLocation)}<br>${escapeHTML(data.scene.physical_state || "距离尚未改变")}<br>${escapeHTML(data.scene.emotional_state || "情绪仍未显露")}</p></article>
    <article class="inspector-card"><h4>与你的关系</h4><p>${escapeHTML(relationship.label || "尚未命名")}<br>${escapeHTML(relationship.notes || "还没有留下共同经历")}</p></article>
    <article class="inspector-card"><h4>还在发生</h4><ul class="thread-list">${threads.length ? threads.map(item => `<li>${escapeHTML(item)}</li>`).join("") : "<li>尚未形成牵引</li>"}</ul></article>
    <article class="inspector-card"><h4>${escapeHTML(character.name)}知道或相信的事</h4><ul class="knowledge-list">${data.knowledge.length ? data.knowledge.slice(0,10).map(item => `<li><span>${escapeHTML(item.belief || item.title)}</span><small>${escapeHTML(knowledgeSource(item))}</small></li>`).join("") : "<li>还没有只属于此人的事实</li>"}</ul></article>`;
  $("#inspector").insertAdjacentHTML("afterbegin", '<button id="close-inspector" class="inspector-close icon-button" data-action="close-inspector" aria-label="关闭视角">×</button><button class="soft-button mobile-save-entry" data-action="saves">存档 / 读档</button>');
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
  if (!continuation) live.messages.set("pending-user", {id:"pending-user", role:"user", content, status:"pending"});
  live.messages.set("pending-assistant", {id:"pending-assistant", role:"assistant", content:"", status:"streaming"});
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
    $(".message-body", node).classList.toggle("typing-caret", live.messages.get(assistantId).status === "streaming");
    if (follow) scroller.scrollTop = scroller.scrollHeight;
    updateLatestButton($("#chat-view"));
  };
  try {
    const response = await appFetch(`/api/conversations/${conversationId}/turn`, {
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({content, continuation}),
    });
    if (!response.ok || !response.body) throw new Error("没有接到世界的回应");
    await readStream(response, event => {
      if (event.type === "user") {
        acknowledged = true;
        const waiting = live.messages.get("pending-assistant");
        live.messages.delete("pending-user"); live.messages.delete("pending-assistant");
        live.messages.set(event.message.id, event.message);
        if (waiting) live.messages.set("pending-assistant", waiting);
        repaint();
      }
      if (event.type === "assistant_start") { live.messages.delete("pending-assistant"); assistantId = event.message.id; live.messages.set(assistantId, event.message); repaint(); }
      if (event.type === "delta" && assistantId) {
        live.messages.get(assistantId).content = event.full;
        if (!frame) frame = requestAnimationFrame(flush);
      }
      if (event.type === "assistant_complete" && assistantId) {
        if (frame) cancelAnimationFrame(frame); frame = 0;
        if (typeof event.content === "string") live.messages.get(assistantId).content = event.content;
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
    if (!acknowledged) { state.conversationDrafts.set(conversationId, content); live.messages.delete("pending-user"); }
    toast(error.message);
  } finally {
    if (frame) cancelAnimationFrame(frame);
    live.messages.delete("pending-assistant");
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

function selectTemplate(templateId, fill = true) {
  const form = $("#world-form");
  form.elements.template_id.value = templateId;
  $$('[data-template]').forEach(button => button.classList.toggle("active", button.dataset.template === templateId));
  if (!fill) return;
  const values = templateId ? state.templates.find(item => item.id === templateId).world : {name:"", premise:"", opening_narration:"", atmosphere:"", rules:"所有角色均为成年人；角色拥有自主意志；意图不是事实。", current_time:""};
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
    state.viewMemory.clear();
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
  const portrait = event.target.closest("[data-portrait]");
  if (portrait) openCharacterPortrait(portrait.dataset.portrait);
  const template = event.target.closest("[data-template]")?.dataset.template;
  if (template !== undefined) selectTemplate(template);
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "retry-preview") { previewErrors.delete(state.world?.world.id); ensureTimePreview(); }
  if (action === "saves") openSaves().catch(error => toast(error.message));
  if (action === "close-saves") closeDialog("#saves-dialog");
  if (action === "switch-space") {
    if (worldBusy()) { toast("请等当前回合完成后切换"); return; }
    fetch(appPath("/logout"), {method:"POST"}).then(response => {
      if (!response.ok) throw new Error("退出失败，请重试");
      location.replace(appPath("/login"));
    }).catch(error => toast(error.message));
  }
  if (action === "edit-player") {
    if (worldBusy() || pendingSettlements().length) { toast("请等这一轮记录完成"); return; }
    const profile = state.world.player_profile || {};
    for (const key of ["name","age","pronouns","appearance","identity"]) $("#player-form").elements[key].value = profile[key] ?? "";
    showDialog("#player-dialog");
  }
  if (action === "close-player") closeDialog("#player-dialog");
  if (action === "close-portrait") closeDialog("#portrait-dialog");
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

$("#player-form").onsubmit = async event => {
  event.preventDefault();
  const form = event.currentTarget;
  await formAction(form, form.querySelector('[type="submit"]'), "正在保存…", async () => {
    const values = Object.fromEntries(new FormData(form));
    values.age = values.age ? Number(values.age) : null;
    state.world.player_profile = await api(`/api/worlds/${state.world.world.id}/player-profile`, {method:"PUT", body:JSON.stringify(values)});
    closeDialog("#player-dialog"); toast("角色已保存");
  });
};

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
