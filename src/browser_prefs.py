"""Per-visitor settings, kept in each visitor's own browser storage (localStorage).

Why: when several people use the same Cloudzy server, a settings file on the
server would be shared by all of them. Here, every visitor's settings stay in
their own browser, and the server only holds a copy for the current session.

How it works (one invisible component, used two ways):
- **Read, once per session:** before any page runs, the component reads the
  stored JSON and sends it back. Pages wait for it, so a saved home city or
  "detect my location" choice applies from the first screen. If the browser
  never answers, the visitor can continue with default settings.
- **Write, after each change:** `src.preferences.save` stages the new settings
  in the session, and `flush()` sends them to the browser. Flushing happens at
  the start of the next run (covering widget callbacks and saves followed by a
  rerun), or straight away when a page calls `flush()` itself.

If the browser blocks site storage, settings work for the current session only,
and nothing is written. Settings that were never read (the visitor chose to
continue without them) are never written either, so a stored copy can't be
overwritten by defaults.
"""

from __future__ import annotations

import json

STORAGE_KEY = "cloudzy-preferences-v1"

_RAW = "_cz_prefs"            # the current settings (dict) for this session
_STATE = "_cz_prefs_state"    # "loading", "loaded" or "skipped"
_STORAGE_OK = "_cz_prefs_storage_ok"
_DIRTY = "_cz_prefs_dirty"
_SLOT = "_cz_prefs_slot"
_WRITES = "_cz_prefs_writes"

JS = r"""
export default function (component) {
  const { data, setTriggerValue } = component;
  const KEY = "cloudzy-preferences-v1";
  if (!data) return;
  if (data.mode === "read") {
    let value = null, available = true;
    try { value = window.localStorage.getItem(KEY); } catch (e) { available = false; }
    setTriggerValue("loaded", { value: value, available: available });
  } else if (data.mode === "write" && typeof data.value === "string") {
    try { window.localStorage.setItem(KEY, data.value); } catch (e) {}
  }
}
"""

_component = None


def _st():
    import streamlit as st
    return st


def _mount(key: str, data: dict, **callbacks):
    global _component
    st = _st()
    if _component is None:
        _component = st.components.v2.component("cloudzy_preferences", js=JS)
    return _component(key=key, data=data, **callbacks)


# --- Session state (pure helpers, tested without a browser) -------------------------------------

def current(state=None) -> dict:
    state = _st().session_state if state is None else state
    raw = state.get(_RAW)
    return raw if isinstance(raw, dict) else {}


def stage(data: dict, state=None) -> None:
    state = _st().session_state if state is None else state
    state[_RAW] = data
    state[_DIRTY] = True


def apply_loaded(payload, state) -> None:
    """Take the browser's answer to the read request. Anything unusable means 'no saved settings'."""
    from src import preferences as P
    available = not (isinstance(payload, dict) and payload.get("available") is False)
    value = payload.get("value") if isinstance(payload, dict) else None
    if state.get(_DIRTY):
        pass  # the visitor already changed something this session: keep that, it will be written next
    else:
        state[_RAW] = P.to_dict(P.parse_json(value)) if isinstance(value, str) else {}
    state[_STATE] = "loaded"
    state[_STORAGE_OK] = available


def skip(state=None) -> None:
    state = _st().session_state if state is None else state
    state[_STATE] = "skipped"
    state[_STORAGE_OK] = False


def ready(state) -> bool:
    return state.get(_STATE) in ("loaded", "skipped")


def storage_available(state=None) -> bool | None:
    """True/False once known; None in file mode or before the browser answered."""
    state = _st().session_state if state is None else state
    return state.get(_STORAGE_OK)


def pending_write(state) -> str | None:
    """The JSON to write to the browser, if anything changed and writing is allowed."""
    if not state.get(_DIRTY) or state.get(_STATE) != "loaded" or not state.get(_STORAGE_OK):
        return None
    return json.dumps(current(state), ensure_ascii=False, separators=(",", ":"))


# --- Streamlit wiring --------------------------------------------------------------------------

def begin() -> bool:
    """Call once per run, before the page. Returns False while waiting for the browser."""
    from src import preferences as P
    if P.store() != "browser":
        return True
    st = _st()
    state = st.session_state
    state[_SLOT] = st.empty()
    if not ready(state):
        result = _mount("cloudzy_prefs_read", {"mode": "read"}, on_loaded_change=lambda: None)
        payload = getattr(result, "loaded", None)
        if payload is not None:
            apply_loaded(payload, state)
        else:
            state.setdefault(_STATE, "loading")
            st.info("Loading your settings from this browser…", icon="⏳")
            st.button("Continue without saved settings", on_click=skip,
                      help="Use default settings for now. Changes then last only until you close this tab.")
            return False
    flush()
    return True


def flush() -> None:
    """Send changed settings to the browser now (no-op in file mode, or when nothing changed)."""
    from src import preferences as P
    if P.store() != "browser":
        return
    state = _st().session_state
    text = pending_write(state)
    if text is None:
        return
    slot = state.get(_SLOT)
    if slot is None:
        return
    n = state.get(_WRITES, 0) + 1
    state[_WRITES] = n
    with slot:
        _mount(f"cloudzy_prefs_write_{n}", {"mode": "write", "value": text})
    state[_DIRTY] = False
