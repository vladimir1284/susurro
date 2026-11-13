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
  mic: document.getElementById("lane-mic"),
  far: document.getElementById("lane-far"),
  transcript: document.getElementById("transcript"),
  hintBody: document.getElementById("hint-body"),
  hintMeta: document.getElementById("hint-meta"),
  errors: document.getElementById("errors"),
  start: document.getElementById("start"),
  hint: document.getElementById("hint-button"),
  stop: document.getElementById("stop"),
};

let state = initial();
let socket = null;

function send(message) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/console?token=${TOKEN}`);
  socket.addEventListener("message", (event) => {
    state = apply(state, JSON.parse(event.data));
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

  renderTranscript();
  renderHint();

  el.start.disabled = isLive(state);
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
  if (PROFILE) {
    send({ type: "session.config", profile_id: PROFILE, language: LANGUAGE, mode: MODE });
  }
  send({ type: "session.start" });
});
el.stop.addEventListener("click", () => send({ type: "session.stop" }));
el.hint.addEventListener("click", () => send({ type: "hint.request", mode: MODE }));

// Space is the hint key: it is the largest target on any keyboard and can be
// hit without looking down.
document.addEventListener("keydown", (event) => {
  if (event.code === "Space" && event.target === document.body) {
    event.preventDefault();
    if (isLive(state)) send({ type: "hint.request", mode: MODE });
  }
});

render();
connect();
