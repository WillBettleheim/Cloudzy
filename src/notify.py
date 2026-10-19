"""Optional browser notifications for Cloudzy alerts (Notification API).

- **Permission:** requested only when the user clicks the button inside this
  component, because browsers require a real user gesture. It's never requested
  automatically or repeatedly.
- **One notification per alert:** each alert has a stable key. The key is used as
  the notification `tag`, and keys are remembered in the browser's
  localStorage, read and written safely.
- **Only while the tab is open:** Cloudzy has no service worker or push server,
  so it doesn't promise background notifications.
- **Unsupported or blocked:** the component says so, and alerts stay on the page.
"""

from __future__ import annotations

JS = r"""
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  let box = parentElement.querySelector(".cz-notify");
  if (!box) { box = document.createElement("div"); box.className = "cz-notify"; parentElement.appendChild(box); }
  const say = (html) => { box.innerHTML = html; };
  if (!("Notification" in window)) {
    say("<p>Browser notifications aren't supported here. Alerts stay on this page.</p>");
    return;
  }
  const seenKey = "cloudzy-notified-alerts";
  const readSeen = () => { try { return JSON.parse(localStorage.getItem(seenKey) || "[]"); } catch (e) { return []; } };
  const writeSeen = (keys) => { try { localStorage.setItem(seenKey, JSON.stringify(keys.slice(-200))); } catch (e) {} };
  const notifyNew = () => {
    const seen = readSeen();
    for (const a of (data && data.alerts) || []) {
      if (seen.includes(a.key)) continue;
      try { new Notification(a.title, { body: a.body, tag: a.key }); } catch (e) { return; }
      seen.push(a.key);
    }
    writeSeen(seen);
  };
  const render = () => {
    const p = Notification.permission;
    if (p === "granted") {
      say("<p>🔔 Browser notifications are on: each new Cloudzy alert notifies once, while Cloudzy is open in a tab.</p>");
      notifyNew();
    } else if (p === "denied") {
      say("<p>Notifications are blocked for this site in your browser settings. Alerts stay on this page.</p>");
    } else {
      say('<button type="button" class="cz-notify-btn">🔔 Enable browser notifications</button>' +
          "<p>Your browser will ask for permission. Notifications only appear while Cloudzy is open in a tab.</p>");
      const btn = box.querySelector("button");
      btn.onclick = async () => {
        btn.disabled = true;
        try { await Notification.requestPermission(); } catch (e) {}
        setTriggerValue("permission", Notification.permission);
        render();
      };
    }
  };
  render();
}
"""

CSS = """
.cz-notify { font-size: .9rem; }
.cz-notify p { margin: 4px 0; }
.cz-notify-btn { font: inherit; padding: 6px 14px; border-radius: 999px; cursor: pointer;
  border: 1px solid rgba(57,135,229,.6); background: rgba(57,135,229,.12); color: inherit; }
.cz-notify-btn:focus-visible { outline: 3px solid #3987e5; outline-offset: 2px; }
"""

_component = None


def alert_payload(alerts) -> list[dict]:
    """Plain-text notification content for each alert, keyed by its stable dedupe key."""
    return [{"key": a.key, "title": f"Cloudzy: {a.title} · {a.value}",
             "body": f"{a.period}. {a.reason} (Not an official warning.)"} for a in alerts]


def browser_notifications(alerts) -> str | None:
    """Mount the notification control. Returns a permission result when the user just answered."""
    global _component
    import streamlit as st
    if _component is None:
        _component = st.components.v2.component("cloudzy_notifications", js=JS, css=CSS)
    result = _component(key="cloudzy_notifications", data={"alerts": alert_payload(alerts)},
                        on_permission_change=lambda: None)
    return getattr(result, "permission", None)
