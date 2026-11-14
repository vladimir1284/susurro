// Console wiring: socket in, DOM out. All of the interesting behaviour lives
// in state.js, which has no DOM and is tested on its own.

import { apply, initial, isLive, medianLatency, speaker } from "./state.js";

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
  new: document.getElementById("new"),
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
  recordingPill: document.getElementById("recording-pill"),
  latencyPill: document.getElementById("latency-pill"),
  history: document.getElementById("history"),
  historyPanel: document.getElementById("history-panel"),
  historyList: document.getElementById("history-list"),
  historyEmpty: document.getElementById("history-empty"),
  historyClose: document.getElementById("history-close"),
  exportView: document.getElementById("export-view"),
  exportTitle: document.getElementById("export-title"),
  exportText: document.getElementById("export-text"),
  exportDownload: document.getElementById("export-download"),
  exportClose: document.getElementById("export-close"),
};

// The editor is opened by the user, not by the arrival of a profile: a
// `profile` event also answers a refresh, and popping the panel open under
// someone mid-call would be hostile.
let historyOpen = false;
// Deleting a transcript is not undoable, so the first click arms the button
// and the second does it. A dialog would be heavier; doing it on one click
// would be worse.
let armedDelete = null;
let editorOpen = false;
let loadedProfile = null;
// Which profile the editor is waiting for. Until it arrives the fields are
// disabled: filling them from a late reply would silently discard anything
// typed in the meantime.
let pendingProfileId = null;

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

  renderLatency();
  renderProfiles();
  renderEditor();
  renderHistory();
  renderTranscript();
  renderHint();

  el.profile.disabled = isLive(state) || state.profiles.length === 0;
  el.edit.disabled = !selectedProfile();
  // "New" stays available always, including on a fresh install with no
  // profiles at all -- which is the only way to make the first one.
  el.new.disabled = isLive(state);
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

//: A profile that exists only in the editor until it is saved. Without this
//: there is no way to make the first one: Edit needs something selected, and
//: on a fresh install nothing is.
function blankProfile() {
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
  return {
    id: `profile-${stamp}`,
    label: "New profile",
    language: LANGUAGE,
    mode: MODE || "conversational",
    context: { resume: "", role: "", notes: "" },
  };
}

const EDITOR_FIELDS = [
  "editorLabel", "editorLanguage", "editorMode",
  "editorResume", "editorRole", "editorNotes", "editorSave",
];

//: Under a second is the budget the whole design is built around. Showing it
//: during a call is the only way to know a session is inside it, rather than
//: finding out afterwards that every hint arrived too late to read.
const LATENCY_BUDGET_MS = 1000;

function renderLatency() {
  const median = medianLatency(state);
  el.latencyPill.hidden = median === null;
  if (median === null) return;
  el.latencyPill.textContent = `${median} ms`;
  el.latencyPill.dataset.over = String(median > LATENCY_BUDGET_MS);
}

function renderHistory() {
  // Shown whenever the core is recording, open panel or not. This is the one
  // indicator the user must not have to go looking for.
  el.recordingPill.hidden = !state.recording;
  el.historyPanel.hidden = !historyOpen;
  if (!historyOpen) return;

  const showingExport = state.export !== null;
  el.exportView.hidden = !showingExport;
  el.historyList.hidden = showingExport;

  if (showingExport) {
    el.exportTitle.textContent = state.export.sessionId;
    el.exportText.textContent = state.export.markdown;
    const blob = new Blob([state.export.markdown], { type: "text/markdown" });
    URL.revokeObjectURL(el.exportDownload.href);
    el.exportDownload.href = URL.createObjectURL(blob);
    el.exportDownload.setAttribute(
      "download",
      `susurro-${state.export.sessionId}.md`,
    );
    return;
  }

  el.historyEmpty.hidden = state.sessions.length > 0;
  el.historyEmpty.textContent = state.recording
    ? "Nothing recorded yet."
    : "Recording is off. Enable [session] record in the core's configuration.";

  el.historyList.replaceChildren(
    ...state.sessions.map((session) => sessionRow(session)),
  );
}

function sessionRow(session) {
  const row = document.createElement("li");
  row.dataset.sessionId = session.session_id;

  const when = document.createElement("span");
  when.className = "when";
  when.textContent = session.started_at.replace("T", " ").replace("+00:00", " UTC");

  const about = document.createElement("span");
  about.className = "about";
  about.textContent = `${session.profile_id} · ${session.lines} lines · ${session.hints} hints`;

  const spacer = document.createElement("span");
  spacer.className = "spacer";

  const view = document.createElement("button");
  view.type = "button";
  view.className = "view";
  view.textContent = "Open";
  view.addEventListener("click", () =>
    send({ type: "session.export", session_id: session.session_id }),
  );

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "delete";
  const armed = armedDelete === session.session_id;
  remove.textContent = armed ? "Delete?" : "Delete";
  remove.addEventListener("click", () => {
    if (armedDelete === session.session_id) {
      armedDelete = null;
      send({ type: "session.delete", session_id: session.session_id });
    } else {
      armedDelete = session.session_id;
      render();
    }
  });

  row.append(when, about, spacer, view, remove);
  return row;
}

function renderEditor() {
  el.editor.hidden = !editorOpen;
  if (!editorOpen) return;

  const ready = state.editing !== null && state.editing.id === pendingProfileId;
  for (const field of EDITOR_FIELDS) el[field].disabled = !ready;
  if (!ready) {
    setStatus("loading…", "");
    return;
  }
  if (state.editing === loadedProfile) return;

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

el.history.addEventListener("click", () => {
  historyOpen = true;
  armedDelete = null;
  state = { ...state, export: null };
  send({ type: "sessions.request" });
  render();
});

el.historyClose.addEventListener("click", () => {
  historyOpen = false;
  render();
});

el.exportClose.addEventListener("click", () => {
  state = { ...state, export: null };
  render();
});

el.edit.addEventListener("click", () => {
  const profile = selectedProfile();
  if (!profile) return;
  editorOpen = true;
  loadedProfile = null;
  pendingProfileId = profile.id;
  send({ type: "profile.request", profile_id: profile.id });
  render();
});

el.new.addEventListener("click", () => {
  const draft = blankProfile();
  editorOpen = true;
  loadedProfile = null;
  pendingProfileId = draft.id;
  // Nothing is sent: the profile does not exist on the core until Save. The
  // editor is filled from the draft directly.
  state = { ...state, editing: draft };
  render();
  el.editorLabel.focus();
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
