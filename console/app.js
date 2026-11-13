// Console wiring: socket in, DOM out. All of the interesting behaviour lives
// in state.js, which has no DOM and is tested on its own.

import { apply, initial, isLive, speaker } from "./state.js";

const params = new URLSearchParams(location.search);
const TOKEN = params.get("token") ?? "";
const PROFILE = params.get("profile") ?? "";
const LANGUAGE = params.get("language") ?? "en";
const MODE = params.get("mode") ?? "conversational";

const el = {
  session: document.getElementById("session-pill"),
  profile: document.getElementById("profile"),
  mic: document.getElementById("lane-mic"),
  far: document.getElementById("lane-far"),
  transcript: document.getElementById("transcript"),
  hintBody: document.getElementById("hint-body"),
  hintMeta: document.getElementById("hint-meta"),
  errors: document.getElementById("errors"),
  start: document.getElementById("start"),
  hint: document.getElementById("hint-button"),
  stop: document.getElementById("stop"),
  edit: document.getElementById("edit"),
  editor: document.getElementById("editor"),
  editorLabel: document.getElementById("editor-label"),
  editorLanguage: document.getElementById("editor-language"),
  editorMode: document.getElementById("editor-mode"),
  editorResume: document.getElementById("editor-resume"),
  editorRole: document.getElementById("editor-role"),
  editorNotes: document.getElementById("editor-notes"),
  editorSave: document.getElementById("editor-save"),
  editorClose: document.getElementById("editor-close"),
  editorStatus: document.getElementById("editor-status"),
};

// The editor is opened by the user, not by the arrival of a profile: a
// `profile` event also answers a refresh, and popping the panel open under
// someone mid-call would be hostile.
let editorOpen = false;
let loadedProfile = null;

let state = initial();
let socket = null;

function send(message) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/console?token=${TOKEN}`);
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    state = apply(state, message);
    // A fresh listing right after a save is the acknowledgement: the core
    // sends one to the saving console before broadcasting it.
    if (message.type === "profiles" && el.editorStatus.textContent === "saving…") {
      setStatus("saved", "saved");
    }
    if (message.type === "error" && el.editorStatus.textContent === "saving…") {
      setStatus(message.detail, "error");
    }
    render();
  });
  socket.addEventListener("close", () => {
    // Reconnect quietly. A console that gives up silently in the middle of a
    // call is worse than one that keeps trying.
    setTimeout(connect, 1000);
  });
}

function render() {
  el.session.textContent = state.session;
  el.session.dataset.session = state.session;
  for (const lane of ["mic", "far"]) {
    el[lane].dataset.up = String(state.lanes[lane] === "up");
    el[lane].textContent = `${lane} ${state.lanes[lane] === "up" ? "●" : "○"}`;
  }

  renderProfiles();
  renderEditor();
  renderTranscript();
  renderHint();

  el.profile.disabled = isLive(state) || state.profiles.length === 0;
  el.edit.disabled = !selectedProfile();
  el.start.disabled = isLive(state) || !selectedProfile();
  el.stop.disabled = !isLive(state);
  el.hint.disabled = !isLive(state);

  el.errors.replaceChildren(
    ...state.errors.map((error) => {
      const div = document.createElement("div");
      div.textContent = `${error.code}: ${error.detail}`;
      return div;
    }),
  );
}

// Rebuilding a <select> under an open dropdown closes it, so this only
// touches the DOM when the options have actually changed.
let renderedProfiles = "";

function renderProfiles() {
  // Everything the option renders goes into the signature, not just the id:
  // renaming a profile has to redraw it, and an id-only signature said
  // nothing had changed.
  const signature = state.profiles
    .map((p) => [p.id, p.label, p.language, p.mode].join("\u001f"))
    .join("\u0000");
  if (signature === renderedProfiles) return;
  renderedProfiles = signature;

  const previous = el.profile.value || PROFILE;
  el.profile.replaceChildren(
    ...state.profiles.map((profile) => {
      const option = document.createElement("option");
      option.value = profile.id;
      option.textContent = `${profile.label} · ${profile.language}`;
      option.dataset.language = profile.language;
      option.dataset.mode = profile.mode;
      return option;
    }),
  );
  if (state.profiles.some((p) => p.id === previous)) el.profile.value = previous;
}

function selectedProfile() {
  return state.profiles.find((p) => p.id === el.profile.value) ?? null;
}

function renderEditor() {
  el.editor.hidden = !editorOpen;
  if (!editorOpen || !state.editing || state.editing === loadedProfile) return;

  // Fill the fields once per loaded profile. Rewriting them on every event
  // would move the caret while someone is typing.
  loadedProfile = state.editing;
  el.editorLabel.value = state.editing.label ?? "";
  el.editorLanguage.value = state.editing.language ?? "en";
  el.editorMode.value = state.editing.mode ?? "conversational";
  const context = state.editing.context ?? {};
  el.editorResume.value = context.resume ?? "";
  el.editorRole.value = context.role ?? "";
  el.editorNotes.value = context.notes ?? "";
  setStatus("", "");
}

function setStatus(text, kind) {
  el.editorStatus.textContent = text;
  el.editorStatus.dataset.state = kind;
}

function renderTranscript() {
  // Scroll stays pinned to the bottom unless the reader has scrolled up to
  // look at something, in which case moving it would be rude.
  const pinned =
    el.transcript.scrollHeight - el.transcript.scrollTop - el.transcript.clientHeight < 40;

  const rows = state.lines.map((line) => utterance(line.lane, line.text, false));
  for (const lane of ["far", "mic"]) {
    const partial = state.partials[lane];
    if (partial) rows.push(utterance(lane, partial.text, true));
  }
  el.transcript.replaceChildren(...rows);

  if (pinned) el.transcript.scrollTop = el.transcript.scrollHeight;
}

function utterance(lane, text, partial) {
  const row = document.createElement("div");
  row.className = partial ? "utterance partial" : "utterance";
  row.dataset.lane = lane;

  const who = document.createElement("span");
  who.className = "who";
  who.textContent = speaker(lane);

  const what = document.createElement("span");
  what.className = "what";
  what.textContent = text;

  row.append(who, what);
  return row;
}

function renderHint() {
  el.hintBody.textContent = state.hint?.text ?? "";
  if (!state.hint) {
    el.hintMeta.textContent = "";
    return;
  }
  const parts = [state.hint.trigger, state.hint.mode];
  if (state.hint.failed) parts.push("failed");
  else if (!state.hint.done) parts.push("writing…");
  if (state.hint.ttft_ms != null) parts.push(`${state.hint.ttft_ms} ms to first token`);
  el.hintMeta.textContent = parts.join(" · ");
}

el.start.addEventListener("click", () => {
  const profile = selectedProfile();
  if (!profile) return;
  // The profile decides the language and the hint style. Sending the query
  // string's values instead would let the two disagree, and the core rejects
  // that rather than guessing -- correctly, but the console should not put it
  // in that position.
  send({
    type: "session.config",
    profile_id: profile.id,
    language: profile.language,
    mode: MODE || profile.mode,
  });
  send({ type: "session.start" });
});
el.stop.addEventListener("click", () => send({ type: "session.stop" }));

el.edit.addEventListener("click", () => {
  const profile = selectedProfile();
  if (!profile) return;
  editorOpen = true;
  loadedProfile = null;
  send({ type: "profile.request", profile_id: profile.id });
  render();
});

el.editorClose.addEventListener("click", () => {
  editorOpen = false;
  render();
});

el.editorSave.addEventListener("click", () => {
  if (!state.editing) return;
  setStatus("saving…", "");
  send({
    type: "profile.save",
    profile_id: state.editing.id,
    label: el.editorLabel.value,
    language: el.editorLanguage.value,
    mode: el.editorMode.value,
    context: {
      resume: el.editorResume.value,
      role: el.editorRole.value,
      notes: el.editorNotes.value,
    },
  });
});
el.hint.addEventListener("click", () => send({ type: "hint.request", mode: hintMode() }));

function hintMode() {
  return MODE || selectedProfile()?.mode || "conversational";
}

// Space is the hint key: it is the largest target on any keyboard and can be
// hit without looking down.
document.addEventListener("keydown", (event) => {
  if (event.code === "Space" && event.target === document.body) {
    event.preventDefault();
    if (isLive(state)) send({ type: "hint.request", mode: hintMode() });
  }
});

render();
connect();
