// Pure view model for the console.
//
// Every protocol event goes through `apply`, which returns a new state. No DOM,
// no WebSocket, no timers — so the behaviour that is easy to get wrong (a
// partial that must be replaced rather than appended, a cancelled hint that
// must vanish rather than linger) is testable without a browser.

export const EMPTY = Object.freeze({
  session: "idle",
  lanes: { mic: "down", far: "down" },
  // Committed transcript, oldest first. Never revised.
  lines: [],
  // One open partial per lane, or null. Replaced on every recognizer pass.
  partials: { mic: null, far: null },
  hint: null,
  errors: [],
});

export function initial() {
  return structuredClone(EMPTY);
}

export function apply(state, event) {
  switch (event.type) {
    case "state":
      return {
        ...state,
        session: event.session,
        lanes: { ...event.lanes },
        // Leaving a live session clears what was said. The transcript belongs
        // to the session, and showing the last one behind the next is worse
        // than showing nothing.
        ...(event.session === "idle" || event.session === "configured"
          ? { lines: [], partials: { mic: null, far: null }, hint: null }
          : {}),
      };

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
      // screen competes with whatever comes next.
      if (event.reason === "cancelled") return { ...state, hint: null };
      return {
        ...state,
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

export function isLive(state) {
  return state.session === "live";
}
