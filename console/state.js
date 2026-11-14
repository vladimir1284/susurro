// Pure view model for the console.
//
// Every protocol event goes through `apply`, which returns a new state. No DOM,
// no WebSocket, no timers — so the behaviour that is easy to get wrong (a
// partial that must be replaced rather than appended, a cancelled hint that
// must vanish rather than linger) is testable without a browser.

export const EMPTY = Object.freeze({
  // Whether the socket is up. Without this the console looks exactly the same
  // disconnected as connected: the last state it saw stays on screen, the
  // controls stay enabled, and a press does nothing with no explanation.
  connected: false,
  session: "idle",
  // What the core will load, so the console can offer a choice instead of
  // requiring the id to be known in advance.
  profiles: [],
  lanes: { mic: "down", far: "down" },
  // What the two models are actually doing, as the core reports it.
  stt: "ready",
  llm: "unknown",
  // Committed transcript, oldest first. Never revised.
  lines: [],
  // One open partial per lane, or null. Replaced on every recognizer pass.
  partials: { mic: null, far: null },
  hint: null,
  // The profile open in the editor, or null when it is closed. Held
  // separately from `profiles` because that list carries labels only.
  editing: null,
  saved: null,
  // Recorded sessions, and whether the core is writing them right now. The
  // flag is rendered prominently: a tool that records conversations without
  // saying so is not one anybody should trust.
  // The last few measured times to first token. The median of a handful is
  // what tells you whether a call is inside the latency budget; a single
  // number swings too much to read mid-conversation.
  latencies: [],
  recording: false,
  sessions: [],
  export: null,
  errors: [],
});

export function initial() {
  return structuredClone(EMPTY);
}

export function apply(state, event) {
  switch (event.type) {
    case "connection":
      return { ...state, connected: event.connected };

    case "state":
      return {
        ...state,
        session: event.session,
        lanes: { ...event.lanes },
        stt: event.stt,
        llm: event.llm,
        // Leaving a live session clears what was said. The transcript belongs
        // to the session, and showing the last one behind the next is worse
        // than showing nothing.
        ...(event.session === "idle" || event.session === "configured"
          ? { lines: [], partials: { mic: null, far: null }, hint: null }
          : {}),
      };

    case "profiles":
      return { ...state, profiles: event.profiles };

    case "sessions":
      return { ...state, recording: event.recording, sessions: event.sessions };

    case "export":
      return {
        ...state,
        export: { sessionId: event.session_id, markdown: event.markdown },
      };

    case "profile":
      return { ...state, editing: event.profile, saved: null };

    case "transcript.partial":
      return {
        ...state,
        partials: { ...state.partials, [event.lane]: { text: event.text, t: event.t } },
      };

    case "transcript.final":
      return {
        ...state,
        lines: [
          ...state.lines,
          { lane: event.lane, text: event.text, t: event.t, seq: event.seq },
        ],
        // The final supersedes the partial on its own lane only. The other
        // lane may still have an utterance open.
        partials: { ...state.partials, [event.lane]: null },
      };

    case "hint.start":
      return {
        ...state,
        hint: {
          id: event.hint_id,
          trigger: event.trigger,
          mode: event.mode,
          text: "",
          done: false,
        },
      };

    case "hint.delta":
      // A delta for a superseded hint is discarded rather than appended. Two
      // generations can overlap when a speculative hint is cancelled late.
      if (!state.hint || state.hint.id !== event.hint_id) return state;
      return { ...state, hint: { ...state.hint, text: state.hint.text + event.text } };

    case "hint.end":
      if (!state.hint || state.hint.id !== event.hint_id) return state;
      // A cancelled hint is removed, not greyed out. It was generated for a
      // question the other party has already moved on from, so leaving it on
      // screen competes with whatever comes next. Its latency still counts:
      // the model was measured either way.
      if (event.reason === "cancelled") {
        return {
          ...state,
          latencies:
            event.ttft_ms != null
              ? [...state.latencies, event.ttft_ms].slice(-9)
              : state.latencies,
          hint: null,
        };
      }
      return {
        ...state,
        latencies:
          event.ttft_ms != null
            ? [...state.latencies, event.ttft_ms].slice(-9)
            : state.latencies,
        hint: {
          ...state.hint,
          done: true,
          failed: event.reason === "error",
          tokens: event.tokens,
          ttft_ms: event.ttft_ms,
        },
      };

    case "error":
      return {
        ...state,
        errors: [...state.errors, { code: event.code, detail: event.detail }].slice(-5),
      };

    default:
      return state;
  }
}

export function speaker(lane) {
  return lane === "far" ? "them" : "you";
}

export function medianLatency(state) {
  if (state.latencies.length === 0) return null;
  const ordered = [...state.latencies].sort((a, b) => a - b);
  return ordered[Math.floor(ordered.length / 2)];
}

export function isLive(state) {
  return state.session === "live";
}
