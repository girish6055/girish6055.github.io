const state = { cameras: [], filters: { severity: "", camera: "", analytic: "" } };

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`view-${tab.dataset.view}`).classList.add("active");
  });
});

["severity", "camera", "analytic"].forEach((key) => {
  document.getElementById(`filter-${key}`).addEventListener("change", (event) => {
    state.filters[key] = event.target.value;
    loadEvents();
  });
});
document.getElementById("refresh-events").addEventListener("click", loadEvents);

function fmtUptime(seconds) {
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function loadStatus() {
  try {
    const data = await (await fetch("/api/status")).json();
    state.cameras = data.cameras || [];
    const online = state.cameras.filter((c) => c.status === "online").length;
    document.getElementById("pill-cameras").textContent = `cameras ${online}/${state.cameras.length} online`;
    document.getElementById("pill-uptime").textContent = `uptime ${fmtUptime(data.uptime_seconds || 0)}`;

    state.cameras.forEach((camera) => {
      const card = document.querySelector(`.camera-card[data-camera="${camera.id}"]`);
      if (!card) return;
      const badge = card.querySelector('[data-role="status"]');
      badge.textContent = camera.status + (camera.fps ? ` · ${camera.fps} fps` : "");
      badge.className = `cam-status ${camera.status}`;
      const list = card.querySelector('[data-role="analytics"]');
      list.innerHTML = (camera.analytics_state || [])
        .map((a) => `<li title="${escapeHtml(a.type)}">${escapeHtml(a.title)}: ${escapeHtml(a.status)}</li>`)
        .join("");
    });

    const cameraFilter = document.getElementById("filter-camera");
    if (cameraFilter.options.length <= 1) {
      state.cameras.forEach((camera) => cameraFilter.add(new Option(camera.name, camera.id)));
    }
    const analyticFilter = document.getElementById("filter-analytic");
    if (analyticFilter.options.length <= 1) {
      const types = new Set();
      state.cameras.forEach((c) => (c.analytics_state || []).forEach((a) => types.add(a.type)));
      [...types].sort().forEach((type) => analyticFilter.add(new Option(type, type)));
    }
    renderHealth();
  } catch (err) {
    document.getElementById("pill-cameras").textContent = "engine unreachable";
  }
}

async function loadEvents() {
  const params = new URLSearchParams({ limit: "150" });
  if (state.filters.severity) params.set("severity", state.filters.severity);
  if (state.filters.camera) params.set("camera", state.filters.camera);
  if (state.filters.analytic) params.set("analytic", state.filters.analytic);
  const data = await (await fetch(`/api/events?${params}`)).json();
  const body = document.getElementById("events-body");
  if (!data.events.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty">No events match this filter.</td></tr>';
    return;
  }
  body.innerHTML = data.events.map((event) => {
    const evidence = [];
    if (event.snapshot) evidence.push(`<a href="/snapshots/${event.snapshot}" target="_blank">snapshot</a>`);
    if (event.clip) evidence.push(`<a href="/recordings/${event.clip.replace(/^recordings[\\/]/, "")}" target="_blank">clip</a>`);
    return `<tr class="${event.acknowledged ? "acked" : ""}">
      <td>${escapeHtml(event.ts.replace("T", " "))}</td>
      <td><span class="sev ${escapeHtml(event.severity)}">${escapeHtml(event.severity)}</span></td>
      <td>${escapeHtml(event.camera_name)}</td>
      <td>${escapeHtml(event.analytic)}</td>
      <td><strong>${escapeHtml(event.title)}</strong><br>${escapeHtml(event.message)}</td>
      <td>${evidence.join(" · ") || "&mdash;"}</td>
      <td>${event.acknowledged ? "" : `<button data-ack="${event.id}">Ack</button>`}</td>
    </tr>`;
  }).join("");
  body.querySelectorAll("[data-ack]").forEach((button) => {
    button.addEventListener("click", async () => {
      await fetch(`/api/events/${button.dataset.ack}/ack`, { method: "POST" });
      loadEvents();
    });
  });
}

async function loadSummary() {
  const data = await (await fetch("/api/summary")).json();
  const severity = data.severity_24h || {};
  document.getElementById("pill-warning").textContent = `warnings ${severity.warning || 0}`;
  document.getElementById("pill-critical").textContent = `critical ${severity.critical || 0}`;

  const counters = data.counters_today || [];
  const cards = {};
  counters.forEach((row) => {
    if (row.name !== "in" && row.name !== "out") return;
    const key = `${row.camera_id}|${row.analytic}`;
    cards[key] = cards[key] || { in: 0, out: 0, camera: row.camera_id, analytic: row.analytic };
    cards[key][row.name] = row.value;
  });
  document.getElementById("count-cards").innerHTML = Object.values(cards).map((card) => `
    <div class="card">
      <div class="label">${escapeHtml(card.analytic.replace("_", " "))} · ${escapeHtml(card.camera)}</div>
      <div class="value">${card.in - card.out}</div>
      <div class="label">in ${card.in} · out ${card.out}</div>
    </div>`).join("") || '<div class="card"><div class="label">Today</div><div class="value">0</div><div class="label">no counts yet</div></div>';

  const body = document.getElementById("counters-body");
  body.innerHTML = counters.length ? counters.map((row) => `<tr>
      <td>${escapeHtml(row.camera_id)}</td><td>${escapeHtml(row.analytic)}</td>
      <td>${escapeHtml(row.name)}</td><td>${row.value}</td>
      <td>${escapeHtml((row.updated_at || "").replace("T", " "))}</td></tr>`).join("")
    : '<tr><td colspan="5" class="empty">No counters yet.</td></tr>';
}

function renderHealth() {
  const online = state.cameras.filter((c) => c.status === "online").length;
  document.getElementById("health-cards").innerHTML = `
    <div class="card"><div class="label">Cameras online</div><div class="value">${online}/${state.cameras.length}</div></div>
    <div class="card"><div class="label">Analytics running</div><div class="value">${
      state.cameras.reduce((sum, c) => sum + (c.analytics_state || []).length, 0)}</div></div>
    <div class="card"><div class="label">Frames processed</div><div class="value">${
      state.cameras.reduce((sum, c) => sum + (c.processed || 0), 0)}</div></div>`;
  const body = document.getElementById("health-body");
  body.innerHTML = state.cameras.length ? state.cameras.map((camera) => `<tr>
      <td>${escapeHtml(camera.name)}<br><span class="label">${escapeHtml(camera.id)}</span></td>
      <td>${escapeHtml(camera.status)}${camera.last_error ? `<br><span class="sev critical">${escapeHtml(camera.last_error)}</span>` : ""}</td>
      <td>${camera.fps}</td><td>${escapeHtml(camera.resolution)}</td><td>${camera.inference_ms} ms</td>
      <td>${(camera.analytics_state || []).map((a) => `${escapeHtml(a.type)}: ${escapeHtml(a.status)}`).join("<br>")}</td>
    </tr>`).join("") : '<tr><td colspan="6" class="empty">No cameras configured.</td></tr>';
}

loadStatus(); loadEvents(); loadSummary();
setInterval(loadStatus, 3000);
setInterval(loadEvents, 8000);
setInterval(loadSummary, 10000);
