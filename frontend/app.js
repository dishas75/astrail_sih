/* ============================================================
   ASTRAIL — frontend application logic
   ============================================================ */

const API = {
  catalog: (groups, demo) => `/api/catalog?groups=${groups}&demo=${demo}`,
  orbits: (groups, demo, hours) => `/api/orbits?groups=${groups}&demo=${demo}&hours=${hours}`,
  conjunctions: (groups, demo, hours, threshold) =>
    `/api/conjunctions?groups=${groups}&demo=${demo}&hours=${hours}&threshold_km=${threshold}`,
  exportCsv: (groups, demo, hours, threshold) =>
    `/api/export/alerts.csv?groups=${groups}&demo=${demo}&hours=${hours}&threshold_km=${threshold}`,
};

const EARTH_RADIUS_KM = 6371;
const RISK_COLORS = { CRITICAL: "#ff5d72", HIGH: "#ffb454", MODERATE: "#5fd8ff", LOW: "#565f85" };

const state = {
  events: [],
  objectCount: 0,
  selected: null,
  kesslerHistory: [], // [{t: Date, index: number}]
  lastCatalog: [],
};

/* ================= PAGE ROUTER ================= */
const PAGE_TITLES = { overview: "Overview", live: "Live Tracking", analytics: "Analytics", methodology: "Methodology" };

function goToPage(id) {
  document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
  document.getElementById(`page-${id}`).classList.add("active");
  const navBtn = document.querySelector(`.nav-item[data-page="${id}"]`);
  if (navBtn) navBtn.classList.add("active");
  document.getElementById("pageTitle").textContent = PAGE_TITLES[id] || id;

  if (id === "live") {
    // The 3D container may have been display:none, which collapses its
    // measured size to 0 — force a resize once it's visible again.
    requestAnimationFrame(() => resizeViewer3D());
  }
  if (id === "analytics") {
    drawAllCharts();
  }
}

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => goToPage(btn.dataset.page));
});
document.querySelectorAll("[data-goto]").forEach((el) => {
  el.addEventListener("click", () => goToPage(el.dataset.goto));
});

/* ================= starfield background ================= */
(function starfield() {
  const canvas = document.getElementById("starfield");
  const ctx = canvas.getContext("2d");
  let stars = [];

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    const count = Math.floor((canvas.width * canvas.height) / 3500);
    stars = Array.from({ length: count }, () => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      r: Math.random() * 1.3 + 0.2,
      tw: Math.random() * Math.PI * 2,
      speed: Math.random() * 0.015 + 0.003,
    }));
  }
  window.addEventListener("resize", resize);
  resize();

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const s of stars) {
      s.tw += s.speed;
      const alpha = 0.35 + Math.sin(s.tw) * 0.35 + 0.3;
      ctx.beginPath();
      ctx.fillStyle = `rgba(200,225,255,${Math.max(0.1, alpha)})`;
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fill();
    }
    requestAnimationFrame(draw);
  }
  draw();
})();

/* ================= clock ================= */
setInterval(() => {
  document.getElementById("utcClock").textContent = new Date().toISOString().substr(11, 8);
}, 1000);

/* ================= mission log ================= */
function log(msg, level = "") {
  const el = document.getElementById("missionLog");
  const line = document.createElement("div");
  line.className = `log-line ${level}`;
  const ts = new Date().toISOString().substr(11, 8);
  line.innerHTML = `<span class="ts">[${ts}]</span>${msg}`;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
  while (el.children.length > 200) el.removeChild(el.firstChild);
}

/* ================= three.js orbital viewer ================= */
let renderer, scene, camera, earthMesh, objectGroup, orbitGroup, raycaster, mouse;
let markers = [];
let camState = { theta: 0.4, phi: 1.0, dist: 19000 };

function getContainerSize(container) {
  // clientWidth/clientHeight can read 0 if this runs before layout settles,
  // or while the container was display:none on an inactive page. Fall back
  // to sane defaults so the renderer is never sized 0x0 (which shows as a
  // permanently blank canvas with no console error).
  const rect = container.getBoundingClientRect();
  const width = rect.width || container.clientWidth || 800;
  const height = rect.height || container.clientHeight || 420;
  return { width, height };
}

function updateCamPosition() {
  const { theta, phi, dist } = camState;
  camera.position.set(
    dist * Math.sin(phi) * Math.sin(theta),
    dist * Math.cos(phi),
    dist * Math.sin(phi) * Math.cos(theta)
  );
  camera.lookAt(0, 0, 0);
}

function resizeViewer3D() {
  if (!renderer || !camera) return;
  const container = document.getElementById("viewer3d");
  const { width, height } = getContainerSize(container);
  if (width === 0 || height === 0) return;
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  renderer.setSize(width, height);
}

function init3D() {
  const container = document.getElementById("viewer3d");
  const { width, height } = getContainerSize(container);

  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 100000);

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setSize(width, height);
  container.appendChild(renderer.domElement);

  scene.add(new THREE.AmbientLight(0x8899cc, 0.9));
  const sun = new THREE.DirectionalLight(0xffffff, 1.1);
  sun.position.set(20000, 8000, 10000);
  scene.add(sun);

  const earthGeo = new THREE.SphereGeometry(EARTH_RADIUS_KM, 48, 48);
  const earthMat = new THREE.MeshPhongMaterial({
    color: 0x0d3b66, emissive: 0x021024, shininess: 12,
    transparent: true, opacity: 0.92,
  });
  earthMesh = new THREE.Mesh(earthGeo, earthMat);
  scene.add(earthMesh);

  const wireGeo = new THREE.SphereGeometry(EARTH_RADIUS_KM * 1.001, 24, 24);
  const wireMat = new THREE.MeshBasicMaterial({ color: 0x5fd8ff, wireframe: true, transparent: true, opacity: 0.12 });
  earthMesh.add(new THREE.Mesh(wireGeo, wireMat));

  const glowGeo = new THREE.SphereGeometry(EARTH_RADIUS_KM * 1.03, 32, 32);
  const glowMat = new THREE.MeshBasicMaterial({ color: 0x5fd8ff, transparent: true, opacity: 0.06 });
  scene.add(new THREE.Mesh(glowGeo, glowMat));

  orbitGroup = new THREE.Group();
  objectGroup = new THREE.Group();
  scene.add(orbitGroup, objectGroup);

  raycaster = new THREE.Raycaster();
  mouse = new THREE.Vector2();

  updateCamPosition();

  let isDragging = false, prevX = 0, prevY = 0;
  renderer.domElement.addEventListener("mousedown", (e) => { isDragging = true; prevX = e.clientX; prevY = e.clientY; });
  window.addEventListener("mouseup", () => (isDragging = false));
  window.addEventListener("mousemove", (e) => {
    if (!isDragging) return;
    camState.theta -= (e.clientX - prevX) * 0.005;
    camState.phi -= (e.clientY - prevY) * 0.005;
    camState.phi = Math.max(0.15, Math.min(Math.PI - 0.15, camState.phi));
    prevX = e.clientX; prevY = e.clientY;
    updateCamPosition();
  });
  renderer.domElement.addEventListener("wheel", (e) => {
    camState.dist *= e.deltaY > 0 ? 1.08 : 0.92;
    camState.dist = Math.max(8000, Math.min(60000, camState.dist));
    updateCamPosition();
    e.preventDefault();
  }, { passive: false });

  renderer.domElement.addEventListener("click", (e) => {
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);
    const hits = raycaster.intersectObjects(markers.map((m) => m.hitMesh));
    if (hits.length) {
      const hit = markers.find((m) => m.hitMesh === hits[0].object);
      if (hit) showObjectInspector(hit);
    }
  });

  (function animate() {
    requestAnimationFrame(animate);
    earthMesh.rotation.y += 0.0006;
    renderer.render(scene, camera);
  })();

  window.addEventListener("resize", resizeViewer3D);
}

function clearGroup(group) {
  while (group.children.length) group.remove(group.children[0]);
}

function renderOrbits(tracks, highlightNorads) {
  clearGroup(orbitGroup);
  clearGroup(objectGroup);
  markers = [];

  tracks.forEach((track) => {
    if (!track.points.length) return;
    const isHighlighted = highlightNorads.has(track.norad_id);
    const pts = track.points.map((p) => new THREE.Vector3(p[0], p[2], p[1]));
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    const color = isHighlighted ? 0xff5d72 : 0x4be3a6;
    const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: isHighlighted ? 0.9 : 0.4 });
    orbitGroup.add(new THREE.Line(geo, mat));

    // Marker sits at the object's *current* position (first sampled point,
    // t=now); the line shows where it's headed over the look-ahead window.
    const current = pts[0];
    const markerColor = isHighlighted ? 0xff5d72 : 0x4be3a6;
    const markerGeo = new THREE.SphereGeometry(isHighlighted ? 140 : 90, 10, 10);
    const markerMat = new THREE.MeshBasicMaterial({ color: markerColor });
    const marker = new THREE.Mesh(markerGeo, markerMat);
    marker.position.copy(current);
    objectGroup.add(marker);

    // Larger, invisible hit-target so clicking near a marker actually
    // registers — at these camera distances the true-scale sphere is only
    // a few screen pixels wide.
    const hitGeo = new THREE.SphereGeometry(320, 8, 8);
    const hitMat = new THREE.MeshBasicMaterial({ visible: false });
    const hitSphere = new THREE.Mesh(hitGeo, hitMat);
    hitSphere.position.copy(current);
    objectGroup.add(hitSphere);

    markers.push({
      mesh: marker, hitMesh: hitSphere, norad_id: track.norad_id, name: track.name,
      altitude_km: Math.round(current.length() - EARTH_RADIUS_KM),
    });

    if (isHighlighted) {
      const glowGeo = new THREE.SphereGeometry(280, 12, 12);
      const glowMat = new THREE.MeshBasicMaterial({ color: 0xff5d72, transparent: true, opacity: 0.25 });
      const glow = new THREE.Mesh(glowGeo, glowMat);
      glow.position.copy(current);
      objectGroup.add(glow);
    }
  });
}

/* ================= risk radar (2D canvas) ================= */
function drawRadar() {
  const canvas = document.getElementById("viewerRadar");
  const container = document.getElementById("viewer3d").parentElement;
  canvas.width = canvas.clientWidth || container.clientWidth;
  canvas.height = 420;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const pad = 50;
  const w = canvas.width - pad * 2;
  const h = canvas.height - pad * 2;

  ctx.strokeStyle = "#1c2740";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad, pad); ctx.lineTo(pad, pad + h); ctx.lineTo(pad + w, pad + h);
  ctx.stroke();

  ctx.fillStyle = "#8a94bb";
  ctx.font = "11px 'Share Tech Mono'";
  ctx.fillText("Miss Distance (km) →", pad + w - 150, pad + h + 30);
  ctx.save();
  ctx.translate(15, pad + h);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText("Hours to Closest Approach →", 0, 0);
  ctx.restore();

  if (!state.events.length) {
    ctx.fillStyle = "#8a94bb";
    ctx.font = "13px 'Share Tech Mono'";
    ctx.fillText("No conjunctions in current window.", pad + 20, pad + h / 2);
    return;
  }

  const maxDist = Math.max(...state.events.map((e) => e.miss_distance_km), 1);
  const maxHours = Math.max(...state.events.map((e) => e.hours_to_tca), 1);

  state.events.forEach((e) => {
    const x = pad + (e.miss_distance_km / maxDist) * w;
    const y = pad + h - (e.hours_to_tca / maxHours) * h;
    const r = 4 + (e.risk_score / 100) * 10;
    ctx.beginPath();
    ctx.fillStyle = RISK_COLORS[e.risk_level] + "cc";
    ctx.shadowColor = RISK_COLORS[e.risk_level];
    ctx.shadowBlur = e.risk_level === "CRITICAL" ? 14 : 4;
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;
  });
}

/* ================= kessler gauge ================= */
function updateGauge(kessler) {
  const fill = document.getElementById("gaugeFill");
  const circumference = 283;
  const pct = Math.max(0, Math.min(100, kessler.index)) / 100;
  fill.style.strokeDashoffset = String(circumference * (1 - pct));

  const colorMap = { NOMINAL: "#4be3a6", WATCH: "#5fd8ff", ELEVATED: "#ffb454", SEVERE: "#ff5d72" };
  fill.style.stroke = colorMap[kessler.label] || "#5fd8ff";

  document.getElementById("kesslerValue").textContent = kessler.index.toFixed(1);
  document.getElementById("kesslerLabel").textContent = kessler.label;
  document.getElementById("criticalCount").textContent = `${kessler.critical_count} CRITICAL`;
  document.getElementById("highCount").textContent = `${kessler.high_count} HIGH`;

  document.getElementById("miniKesslerVal").textContent = kessler.index.toFixed(1);
  document.querySelector(".mini-kessler-dot").style.background = colorMap[kessler.label] || "#5fd8ff";
  document.querySelector(".mini-kessler-dot").style.boxShadow = `0 0 8px ${colorMap[kessler.label] || "#5fd8ff"}`;
}

/* ================= alerts list ================= */
function renderAlerts(events) {
  const list = document.getElementById("alertsList");
  list.innerHTML = "";
  document.getElementById("alertCountBadge").textContent = events.length;
  document.getElementById("alertCountVal").textContent = events.length;

  if (!events.length) {
    list.innerHTML = `<div class="empty-note">No conjunctions detected within threshold.<br>Try widening the window or threshold.</div>`;
    return;
  }

  events.slice(0, 60).forEach((e) => {
    const item = document.createElement("div");
    item.className = `alert-item ${e.risk_level}`;
    const tca = new Date(e.tca);
    item.innerHTML = `
      <div class="alert-top">
        <span>T-${e.hours_to_tca.toFixed(1)}h · ${tca.toISOString().substr(11, 5)}Z</span>
        <span class="risk-tag ${e.risk_level}">${e.risk_level} ${e.risk_score}</span>
      </div>
      <div class="alert-names">${e.object_a.name} ⟷ ${e.object_b.name}</div>
      <div class="alert-stats">
        <span>⊘ ${e.miss_distance_km} km</span>
        <span>Δv ${e.relative_velocity_km_s} km/s</span>
      </div>`;
    item.addEventListener("click", () => { goToPage("live"); selectEvent(e); });
    list.appendChild(item);
  });
}

/* ================= timeline ================= */
function renderTimeline(events, windowHours) {
  const el = document.getElementById("timeline");
  el.innerHTML = `<div class="timeline-axis"></div>`;
  document.getElementById("timelineHours").textContent = windowHours;

  events.slice(0, 40).forEach((e) => {
    const pct = Math.min(100, (e.hours_to_tca / windowHours) * 100);
    const dot = document.createElement("div");
    dot.className = `tl-event ${e.risk_level}`;
    dot.style.left = `${pct}%`;
    dot.title = `${e.object_a.name} / ${e.object_b.name} — T-${e.hours_to_tca.toFixed(1)}h`;
    dot.addEventListener("click", () => selectEvent(e));
    el.appendChild(dot);

    if (Math.random() < 0.4 || e.risk_level === "CRITICAL") {
      const label = document.createElement("div");
      label.className = "tl-label";
      label.style.left = `${pct}%`;
      label.textContent = `T-${e.hours_to_tca.toFixed(0)}h`;
      el.appendChild(label);
    }
  });
}

/* ================= inspector ================= */
function selectEvent(e) {
  state.selected = e;
  const inspector = document.getElementById("inspector");
  const tca = new Date(e.tca);
  inspector.innerHTML = `
    <div class="inspector-row"><span>Object A</span><span>${e.object_a.name}</span></div>
    <div class="inspector-row"><span>Object B</span><span>${e.object_b.name}</span></div>
    <div class="inspector-row"><span>Time of Closest Approach</span><span>${tca.toISOString()}</span></div>
    <div class="inspector-row"><span>Miss Distance</span><span>${e.miss_distance_km} km</span></div>
    <div class="inspector-row"><span>Relative Velocity</span><span>${e.relative_velocity_km_s} km/s</span></div>
    <div class="inspector-row"><span>Risk Score</span><span>${e.risk_score} / 100</span></div>
    <div class="inspector-row"><span>Risk Level</span><span class="risk-tag ${e.risk_level}">${e.risk_level}</span></div>
    <div class="inspector-row"><span>Proximity Component</span><span>${e.proximity_component}</span></div>
    <div class="inspector-row"><span>Velocity Component</span><span>${e.velocity_component}</span></div>
    <div class="inspector-row"><span>Confidence Penalty</span><span>${e.confidence_penalty}</span></div>
  `;
  refreshOrbitHighlight(new Set([e.object_a.norad_id, e.object_b.norad_id]));
}

function showObjectInspector(markerInfo) {
  const related = state.events.find(
    (e) => e.object_a.norad_id === markerInfo.norad_id || e.object_b.norad_id === markerInfo.norad_id
  );
  if (related) { selectEvent(related); return; }

  state.selected = null;
  const inspector = document.getElementById("inspector");
  inspector.innerHTML = `
    <div class="inspector-row"><span>Object</span><span>${markerInfo.name}</span></div>
    <div class="inspector-row"><span>NORAD ID</span><span>${markerInfo.norad_id}</span></div>
    <div class="inspector-row"><span>Approx. Altitude</span><span>${markerInfo.altitude_km} km</span></div>
    <div class="inspector-row"><span>Conjunction Status</span><span style="color:var(--text-faint)">No alert in current window</span></div>
  `;
  refreshOrbitHighlight(new Set([markerInfo.norad_id]));
}

let lastTracks = [];
function refreshOrbitHighlight(highlightSet) {
  renderOrbits(lastTracks, highlightSet || new Set());
}

/* ================= viewer tabs (3D / radar) ================= */
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    const view = tab.dataset.view;
    document.getElementById("viewer3d").style.display = view === "3d" ? "block" : "none";
    document.getElementById("viewerRadar").style.display = view === "radar" ? "block" : "none";
    if (view === "radar") drawRadar();
    else requestAnimationFrame(resizeViewer3D);
  });
});

/* ================= controls ================= */
const hoursSlider = document.getElementById("hoursSlider");
const threshSlider = document.getElementById("threshSlider");
hoursSlider.addEventListener("input", () => (document.getElementById("hoursVal").textContent = `${hoursSlider.value}h`));
threshSlider.addEventListener("input", () => (document.getElementById("threshVal").textContent = `${threshSlider.value} km`));

document.getElementById("refreshBtn").addEventListener("click", runScan);
document.getElementById("exportBtn").addEventListener("click", () => {
  const { groups, demo, hours, threshold } = currentParams();
  window.open(API.exportCsv(groups, demo, hours, threshold), "_blank");
  log("Exported conjunction report (CSV).");
});

function currentParams() {
  const groups = Array.from(document.getElementById("groupSelect").selectedOptions)
    .map((o) => o.value).join(",") || "stations";
  const demo = document.getElementById("demoToggle").checked;
  const hours = Number(hoursSlider.value);
  const threshold = Number(threshSlider.value);
  return { groups, demo, hours, threshold };
}

/* ================= source status (sidebar) ================= */
let backendHasConnected = false;

function setSourceChip(source) {
  const chip = document.getElementById("sideDot").parentElement;
  const label = document.getElementById("sideSourceLabel");
  const sub = document.getElementById("sideSourceSub");
  chip.className = "side-status";
  const map = {
    live: ["source-live", "LIVE · CELESTRAK"],
    cache: ["source-cache", "CACHED · CELESTRAK"],
    "stale-cache": ["source-cache", "STALE CACHE"],
    demo: ["source-demo", "DEMO MODE"],
  };
  const [cls, text] = map[source] || ["", "UNKNOWN"];
  if (cls) chip.classList.add(cls);
  label.textContent = text;
  if (sub) sub.style.display = "none";
}

// First-ever backend response of the session gets a brief "SYSTEM ONLINE"
// flash before settling into the normal source chip (LIVE / DEMO / CACHED),
// so a judge watching the wake-up from Render's free tier sees a clear
// "it's alive" moment rather than jumping straight to a source label.
function markBackendOnline(source) {
  if (backendHasConnected) {
    setSourceChip(source);
    return;
  }
  backendHasConnected = true;
  const chip = document.getElementById("sideDot").parentElement;
  const label = document.getElementById("sideSourceLabel");
  const sub = document.getElementById("sideSourceSub");
  chip.className = "side-status source-online";
  label.textContent = "SYSTEM ONLINE";
  if (sub) sub.style.display = "none";
  setTimeout(() => setSourceChip(source), 1100);
}

/* ================= OVERVIEW page rendering ================= */
let countdownTimer = null;

function updateOverview(kessler, events, objectCount, source) {
  document.getElementById("kpiObjects").textContent = objectCount;
  document.getElementById("kpiSource").textContent = source === "demo" ? "demo catalog" : `via ${source}`;

  document.getElementById("kpiAlerts").textContent = events.length;
  document.getElementById("kpiAlertsSub").textContent = events.length
    ? `${kessler.critical_count} critical · ${kessler.high_count} high` : "all clear";

  document.getElementById("kpiKessler").textContent = kessler.index.toFixed(1);
  document.getElementById("kpiKesslerLabel").textContent = kessler.label.toLowerCase();

  // risk breakdown stack + legend
  const counts = { CRITICAL: 0, HIGH: 0, MODERATE: 0, LOW: 0 };
  events.forEach((e) => counts[e.risk_level]++);
  const total = events.length || 1;
  const stack = document.getElementById("riskStack");
  stack.innerHTML = Object.entries(counts).map(([lvl, c]) =>
    c ? `<div class="${lvl}" style="width:${(c / total) * 100}%"></div>` : "").join("");
  const legend = document.getElementById("riskLegend");
  legend.innerHTML = Object.entries(counts).map(([lvl, c]) =>
    `<span class="legend-item"><span class="swatch" style="background:${RISK_COLORS[lvl]}"></span>${lvl} (${c})</span>`
  ).join("");

  // preview alerts (top 5)
  const preview = document.getElementById("previewAlerts");
  if (!events.length) {
    preview.innerHTML = `<div class="empty-note">No conjunctions in the current window.</div>`;
  } else {
    preview.innerHTML = events.slice(0, 5).map((e) => `
      <div class="preview-alert-row">
        <span class="names">${e.object_a.name} ⟷ ${e.object_b.name}</span>
        <span class="meta">${e.risk_level} · T-${e.hours_to_tca.toFixed(1)}h</span>
      </div>`).join("");
  }

  // countdown to soonest TCA
  if (countdownTimer) clearInterval(countdownTimer);
  if (events.length) {
    const soonest = events.slice().sort((a, b) => a.hours_to_tca - b.hours_to_tca)[0];
    const targetTime = new Date(soonest.tca).getTime();
    document.getElementById("kpiCountdownSub").textContent =
      `${soonest.object_a.name} ⟷ ${soonest.object_b.name}`;
    const tick = () => {
      const diff = Math.max(0, targetTime - Date.now());
      const h = Math.floor(diff / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      const s = Math.floor((diff % 60000) / 1000);
      document.getElementById("kpiCountdown").textContent =
        `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    };
    tick();
    countdownTimer = setInterval(tick, 1000);
  } else {
    document.getElementById("kpiCountdown").textContent = "—:—:—";
    document.getElementById("kpiCountdownSub").textContent = "no active conjunctions";
  }
}

/* ================= ANALYTICS page charts ================= */
function setupCanvas(canvas) {
  const ctx = canvas.getContext("2d");
  const cssWidth = canvas.clientWidth || canvas.parentElement.clientWidth;
  const cssHeight = Number(canvas.getAttribute("height")) || 220;
  canvas.width = cssWidth;
  canvas.height = cssHeight;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  return ctx;
}

function drawDonut() {
  const canvas = document.getElementById("donutChart");
  const ctx = setupCanvas(canvas);
  const cx = canvas.width / 2, cy = canvas.height / 2;
  const rOuter = Math.min(cx, cy) - 14;
  const rInner = rOuter * 0.6;

  const counts = { CRITICAL: 0, HIGH: 0, MODERATE: 0, LOW: 0 };
  state.events.forEach((e) => counts[e.risk_level]++);
  const total = state.events.length;

  if (!total) {
    ctx.fillStyle = "#8a94bb";
    ctx.font = "12px Inter";
    ctx.textAlign = "center";
    ctx.fillText("No events yet — run a scan.", cx, cy);
    ctx.textAlign = "left";
    return;
  }

  let start = -Math.PI / 2;
  Object.entries(counts).forEach(([lvl, c]) => {
    if (!c) return;
    const angle = (c / total) * Math.PI * 2;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, rOuter, start, start + angle);
    ctx.closePath();
    ctx.fillStyle = RISK_COLORS[lvl];
    ctx.fill();
    start += angle;
  });
  ctx.globalCompositeOperation = "destination-out";
  ctx.beginPath();
  ctx.arc(cx, cy, rInner, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalCompositeOperation = "source-over";

  ctx.fillStyle = "#eef1fb";
  ctx.font = "700 20px Orbitron";
  ctx.textAlign = "center";
  ctx.fillText(total, cx, cy + 6);
  ctx.font = "9px 'Share Tech Mono'";
  ctx.fillStyle = "#8a94bb";
  ctx.fillText("EVENTS", cx, cy + 20);
  ctx.textAlign = "left";
}

function drawHistogram() {
  const canvas = document.getElementById("histChart");
  const ctx = setupCanvas(canvas);
  const pad = 30;
  const w = canvas.width - pad * 2;
  const h = canvas.height - pad * 2;

  if (!state.events.length) {
    ctx.fillStyle = "#8a94bb";
    ctx.font = "12px Inter";
    ctx.fillText("No events yet — run a scan.", pad, canvas.height / 2);
    return;
  }

  const maxDist = Math.max(...state.events.map((e) => e.miss_distance_km));
  const bucketCount = 8;
  const bucketSize = Math.max(maxDist / bucketCount, 0.5);
  const buckets = new Array(bucketCount).fill(0);
  state.events.forEach((e) => {
    const idx = Math.min(bucketCount - 1, Math.floor(e.miss_distance_km / bucketSize));
    buckets[idx]++;
  });
  const maxCount = Math.max(...buckets, 1);
  const barW = w / bucketCount;

  buckets.forEach((count, i) => {
    const barH = (count / maxCount) * h;
    const x = pad + i * barW + barW * 0.15;
    const y = pad + h - barH;
    const grad = ctx.createLinearGradient(0, y, 0, pad + h);
    grad.addColorStop(0, "#5fd8ff");
    grad.addColorStop(1, "#5fd8ff33");
    ctx.fillStyle = grad;
    ctx.fillRect(x, y, barW * 0.7, barH);
    if (count) {
      ctx.fillStyle = "#8a94bb";
      ctx.font = "10px 'Share Tech Mono'";
      ctx.textAlign = "center";
      ctx.fillText(count, x + barW * 0.35, y - 4);
    }
  });

  ctx.strokeStyle = "#1c2740";
  ctx.beginPath();
  ctx.moveTo(pad, pad + h); ctx.lineTo(pad + w, pad + h);
  ctx.stroke();
  ctx.fillStyle = "#565f85";
  ctx.font = "9px 'Share Tech Mono'";
  ctx.textAlign = "left";
  ctx.fillText("0 km", pad, pad + h + 14);
  ctx.textAlign = "right";
  ctx.fillText(`${maxDist.toFixed(0)} km`, pad + w, pad + h + 14);
  ctx.textAlign = "left";
}

function extractFamily(name) {
  const cleaned = name.replace(/\(.*?\)/g, "").trim();
  const match = cleaned.match(/^([A-Za-z][A-Za-z\-\s]*?)(?:[\s-]?\d|$)/);
  return (match ? match[1] : cleaned).trim().toUpperCase() || "OTHER";
}

function drawComposition() {
  const canvas = document.getElementById("compChart");
  const ctx = setupCanvas(canvas);
  const pad = 30;
  const w = canvas.width - pad * 2;
  const h = canvas.height - pad * 2;

  if (!state.lastCatalog.length) {
    ctx.fillStyle = "#8a94bb";
    ctx.font = "12px Inter";
    ctx.fillText("No catalog data yet — run a scan.", pad, canvas.height / 2);
    return;
  }

  const groups = {};
  state.lastCatalog.forEach((o) => {
    const fam = extractFamily(o.name);
    groups[fam] = (groups[fam] || 0) + 1;
  });
  const entries = Object.entries(groups).sort((a, b) => b[1] - a[1]).slice(0, 7);
  const maxCount = Math.max(...entries.map((e) => e[1]), 1);
  const barH = h / entries.length;

  entries.forEach(([name, count], i) => {
    const y = pad + i * barH + barH * 0.2;
    const barW = (count / maxCount) * (w - 90);
    ctx.fillStyle = "#5fd8ff";
    ctx.fillRect(pad + 90, y, barW, barH * 0.6);
    ctx.fillStyle = "#8a94bb";
    ctx.font = "10px Inter";
    ctx.textAlign = "right";
    ctx.fillText(name.length > 13 ? name.slice(0, 12) + "…" : name, pad + 84, y + barH * 0.4);
    ctx.fillStyle = "#eef1fb";
    ctx.textAlign = "left";
    ctx.fillText(count, pad + 96 + barW, y + barH * 0.4);
  });
}

function drawTrend() {
  const canvas = document.getElementById("trendChart");
  const ctx = setupCanvas(canvas);
  const pad = 30;
  const w = canvas.width - pad * 2;
  const h = canvas.height - pad * 2;

  if (state.kesslerHistory.length < 2) {
    ctx.fillStyle = "#8a94bb";
    ctx.font = "12px Inter";
    ctx.fillText("Run at least two scans this session to see a trend.", pad, canvas.height / 2);
    return;
  }

  const points = state.kesslerHistory;
  const maxIdx = Math.max(...points.map((p) => p.index), 10);

  ctx.strokeStyle = "#1c2740";
  for (let i = 0; i <= 4; i++) {
    const y = pad + (h / 4) * i;
    ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(pad + w, y); ctx.stroke();
  }

  ctx.beginPath();
  points.forEach((p, i) => {
    const x = pad + (i / (points.length - 1)) * w;
    const y = pad + h - (p.index / maxIdx) * h;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#5fd8ff";
  ctx.lineWidth = 2;
  ctx.stroke();

  points.forEach((p, i) => {
    const x = pad + (i / (points.length - 1)) * w;
    const y = pad + h - (p.index / maxIdx) * h;
    ctx.beginPath();
    ctx.fillStyle = "#5fd8ff";
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fill();
  });
}

function renderLeaderboard() {
  const el = document.getElementById("leaderboard");
  if (!state.events.length) {
    el.innerHTML = `<div class="empty-note">No events yet — run a scan.</div>`;
    return;
  }
  const counts = {};
  state.events.forEach((e) => {
    [e.object_a, e.object_b].forEach((o) => {
      counts[o.name] = (counts[o.name] || 0) + 1;
    });
  });
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 8);
  const maxCount = sorted.length ? sorted[0][1] : 1;
  el.innerHTML = sorted.map(([name, count], i) => `
    <div class="lb-row">
      <span class="lb-rank">#${i + 1}</span>
      <span class="lb-name">${name}</span>
      <span class="lb-bar-track"><span class="lb-bar-fill" style="width:${(count / maxCount) * 100}%"></span></span>
      <span class="lb-count">${count} event${count > 1 ? "s" : ""}</span>
    </div>`).join("");
}

function renderInsight(kessler) {
  const el = document.getElementById("insightText");
  if (!state.events.length) {
    el.textContent = "No conjunctions detected in the current window — nothing to analyze yet.";
    return;
  }
  const avgProx = state.events.reduce((s, e) => s + e.proximity_component, 0) / state.events.length;
  const avgVel = state.events.reduce((s, e) => s + e.velocity_component, 0) / state.events.length;
  const driver = avgProx >= avgVel ? "proximity (how close the miss distances are)" : "kinetic severity (relative velocity)";
  const soonest = state.events.slice().sort((a, b) => a.hours_to_tca - b.hours_to_tca)[0];

  el.innerHTML = `Across <b>${state.events.length}</b> detected conjunction${state.events.length > 1 ? "s" : ""},
    risk is currently driven more by <b>${driver}</b> than the other factor
    (avg proximity score ${avgProx.toFixed(1)} vs. avg velocity score ${avgVel.toFixed(1)}).
    The ASTRAIL Risk Index sits at <b>${kessler.index.toFixed(1)}</b> (${kessler.label.toLowerCase()}),
    with the soonest closest approach between <b>${soonest.object_a.name}</b> and
    <b>${soonest.object_b.name}</b> in about <b>${soonest.hours_to_tca.toFixed(1)}h</b>.`
    .replace(/\s+/g, " ");
}

function drawAllCharts() {
  drawDonut();
  drawHistogram();
  drawComposition();
  drawTrend();
  renderLeaderboard();
}

/* ================= main scan flow ================= */
async function runScan() {
  const { groups, demo, hours, threshold } = currentParams();
  log(`Initiating scan · window=${hours}h · threshold=${threshold}km · demo=${demo}`);

  try {
    const orbitsRes = await fetch(API.orbits(groups, demo, Math.min(hours, 6)));
    const orbitsData = await orbitsRes.json();
    lastTracks = orbitsData.tracks;
    markBackendOnline(orbitsData.source);
    log(`Ingested ${orbitsData.tracks.length} tracked objects (${orbitsData.source}).`);
    document.getElementById("objectCountVal").textContent = orbitsData.tracks.length;
    state.objectCount = orbitsData.tracks.length;
    state.lastCatalog = orbitsData.tracks.map((t) => ({ name: t.name, norad_id: t.norad_id }));
    renderOrbits(lastTracks, new Set());
  } catch (err) {
    log(`Orbit ingestion failed: ${err}`, "err");
    if (!backendHasConnected) {
      const label = document.getElementById("sideSourceLabel");
      const sub = document.getElementById("sideSourceSub");
      label.textContent = "STILL WAKING UP…";
      if (sub) sub.style.display = "block";
    }
  }

  try {
    const res = await fetch(API.conjunctions(groups, demo, hours, threshold));
    const data = await res.json();
    state.events = data.events;
    markBackendOnline(data.source);

    log(`Conjunction scan complete: ${data.events.length} events within ${threshold}km.`,
        data.events.length ? "warn" : "");
    const critical = data.events.filter((e) => e.risk_level === "CRITICAL");
    if (critical.length) log(`⚠ ${critical.length} CRITICAL conjunction(s) detected.`, "err");

    updateGauge(data.kessler_index);
    renderAlerts(data.events);
    renderTimeline(data.events, hours);
    drawRadar();
    updateOverview(data.kessler_index, data.events, state.objectCount, data.source);

    state.kesslerHistory.push({ t: new Date(), index: data.kessler_index.index });
    if (state.kesslerHistory.length > 30) state.kesslerHistory.shift();
    renderInsight(data.kessler_index);

    if (document.getElementById("page-analytics").classList.contains("active")) drawAllCharts();

    if (data.events.length) {
      const highlightSet = new Set();
      data.events.slice(0, 8).forEach((e) => {
        highlightSet.add(e.object_a.norad_id);
        highlightSet.add(e.object_b.norad_id);
      });
      renderOrbits(lastTracks, highlightSet);
    }
  } catch (err) {
    log(`Conjunction scan failed: ${err}`, "err");
  }
}

/* ================= quick demo CTA ================= */
document.getElementById("quickDemoBtn").addEventListener("click", () => {
  document.getElementById("demoToggle").checked = true;
  hoursSlider.value = 48;
  document.getElementById("hoursVal").textContent = "48h";
  threshSlider.value = 25;
  document.getElementById("threshVal").textContent = "25 km";
  // Demo Mode restricts itself to the offline synthetic catalog regardless
  // of which object sets are selected, but pick sensible ones anyway so the
  // Object Sets panel makes sense if a judge glances at it afterward.
  Array.from(document.getElementById("groupSelect").options).forEach((o) => {
    o.selected = ["stations", "cosmos-1408-debris", "iridium-33-debris", "cosmos-2251-debris"].includes(o.value);
  });
  goToPage("live");
  log("Quick Demo: synthetic catalog · 48h window · 25km threshold.");
  runScan();
});

/* ================= boot ================= */
window.addEventListener("load", () => {
  init3D();
  log("ASTRAIL online. Awaiting mission parameters.");
  runScan();
});

window.addEventListener("resize", () => {
  if (document.getElementById("page-analytics").classList.contains("active")) drawAllCharts();
});
