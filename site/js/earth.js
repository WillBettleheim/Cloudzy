// Cloudzy static edition: mounts the app's own Earth Intelligence globe (src/earth_view.py, exported unchanged as
// earth-globe.js at build time) with the hazard snapshot the build took (data/earth.json). The globe expects the
// Streamlit components-v2 interface ({data, parentElement, setTriggerValue}); here selections stay inside the globe.
import mountGlobe from "./earth-globe.js";

const host = document.getElementById("globe");
const note = document.getElementById("globe-note");

async function main() {
  let data;
  try {
    const r = await fetch("data/earth.json");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    data = await r.json();
  } catch (e) {
    note.textContent = `The hazard snapshot couldn't be loaded (${e.message}). The globe needs it to draw events.`;
    note.className = "status error";
    note.hidden = false;
    return;
  }
  const root = host.attachShadow({ mode: "open" });
  const style = document.createElement("link");
  style.rel = "stylesheet";
  style.href = "css/earth-globe.css";
  root.append(style);
  const mount = document.createElement("div");
  root.append(mount);
  mountGlobe({ data, parentElement: mount, setTriggerValue: () => {} });
}

main();
