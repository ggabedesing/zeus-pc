const $ = (id) => document.getElementById(id);

let lastScan = null;
let sampleTimer = null;
let processTimer = null;
let sampleIntervalMs = 1000;
let lastProcessSnapshot = null;
let shownHistoryId = null;
const series = { cpu: [], gpu: [], ram: [], temp: [] };
const SERIES_MAX = 90;

let powerPlans = [];
let powerTxCache = [];
let powerPending = null;
let powerBusy = false;
let powerCountdownTimer = null;

const STATUS_META = {
  OK: { label: "OK", cls: "ok" },
  PARTIAL: { label: "Parcial", cls: "warn" },
  REQUIRES_ELEVATION: { label: "Requer admin", cls: "muted" },
  UNSUPPORTED: { label: "Indisponível", cls: "muted" },
  NOT_CHECKED: { label: "Não verificado", cls: "muted" },
  FAILED: { label: "Falhou", cls: "bad" },
};

const CHART_COLORS = { cpu: "#7dff8d", gpu: "#61d6ff", ram: "#b18cff", temp: "#ffd86b" };

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char];
  });
}

function statusMeta(status) {
  return STATUS_META[status] || { label: status || "—", cls: "muted" };
}

function statusPill(status) {
  const meta = statusMeta(status);
  return "<span class='state-pill " + meta.cls + "'>" + escapeHtml(meta.label) + "</span>";
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  setTimeout(function () { el.classList.remove("show"); }, 2400);
}

function go(page) {
  document.querySelectorAll(".page").forEach(function (el) { el.classList.remove("active"); });
  document.querySelectorAll(".nav-item").forEach(function (el) {
    el.classList.toggle("active", el.dataset.page === page);
  });
  $("page-" + page).classList.add("active");
  if (page === "history" && !$("historyList").dataset.loaded) loadHistoryList();
}

document.querySelectorAll(".nav-item").forEach(function (button) {
  button.addEventListener("click", function () { go(button.dataset.page); });
});

document.querySelectorAll("[data-go]").forEach(function (button) {
  button.addEventListener("click", function () { go(button.dataset.go); });
});

function metric(domain, name) {
  if (!lastScan || !lastScan.snapshot) return null;
  const domainItem = lastScan.snapshot.find(function (entry) { return entry.domain === domain; });
  if (!domainItem) return null;
  const metricItem = domainItem.metrics.find(function (entry) { return entry.name === name; });
  return metricItem ? metricItem.value : null;
}

function setBar(id, value, max) {
  max = max || 100;
  const el = $(id);
  if (!el) return;
  const numeric = Number(value || 0);
  const pct = Math.max(0, Math.min(100, numeric / max * 100));
  el.style.width = pct + "%";
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "—";
  if (bytes >= 1024 * 1024 * 1024) return (bytes / (1024 * 1024 * 1024)).toFixed(2) + " GB";
  if (bytes >= 1024 * 1024) return Math.round(bytes / (1024 * 1024)) + " MB";
  return Math.round(bytes / 1024) + " KB";
}

function recommendationNode(item) {
  const div = document.createElement("div");
  const cls = item.status === "RECOMMENDED" ? "bad" : item.status === "BLOCKED" ? "warn" : "ok";
  div.className = "recommendation";
  div.innerHTML =
    "<div><h4>" + escapeHtml(item.title) + "</h4><p>" + escapeHtml(item.reason) +
    "</p></div><span class='state-pill " + cls + "'>" + escapeHtml(item.status) + "</span>";
  return div;
}

function renderRecommendations() {
  const list = lastScan && lastScan.recommendations ? lastScan.recommendations : [];
  const home = $("homeRecommendations");
  const full = $("optimizationList");
  home.innerHTML = "";
  full.innerHTML = "";

  if (!list.length) {
    home.className = "recommendation-list empty-state";
    home.textContent = "Nenhuma recomendação nesta análise.";
  } else {
    home.className = "recommendation-list";
    list.slice(0, 3).forEach(function (item) { home.appendChild(recommendationNode(item)); });
  }
  if (!list.length) {
    full.className = "recommendation-list empty-state";
    full.textContent = "Nenhuma recomendação nesta análise.";
  } else {
    full.className = "recommendation-list";
    list.forEach(function (item) { full.appendChild(recommendationNode(item)); });
  }

  $("recommendationCount").textContent = list.filter(function (x) { return x.status === "RECOMMENDED"; }).length;
  $("blockedCount").textContent = list.filter(function (x) { return x.status === "BLOCKED"; }).length;
  $("okCount").textContent = list.filter(function (x) { return x.status === "ALREADY_CORRECT"; }).length;
}

function domainCounts(snapshot) {
  const counts = { ok: 0, warn: 0, muted: 0, bad: 0 };
  (snapshot || []).forEach(function (item) {
    const meta = statusMeta(item.status);
    counts[meta.cls] += 1;
  });
  return counts;
}

function renderDomainSummary(elementId, snapshot) {
  const box = $(elementId);
  if (!box) return;
  const counts = domainCounts(snapshot);
  if (!(snapshot || []).length) {
    box.className = "domain-summary empty-state";
    box.textContent = "Faça uma análise para ver o estado de cada área.";
    return;
  }
  box.className = "domain-summary";
  box.innerHTML =
    "<span class='sum-chip ok'><b>" + counts.ok + "</b> OK</span>" +
    "<span class='sum-chip warn'><b>" + counts.warn + "</b> parciais</span>" +
    "<span class='sum-chip muted'><b>" + counts.muted + "</b> sem leitura</span>" +
    "<span class='sum-chip bad'><b>" + counts.bad + "</b> falhas</span>";
}

function metricValueText(value) {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "Sim" : "Não";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : String(Math.round(value * 100) / 100);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function domainCard(item) {
  const details = document.createElement("details");
  details.className = "domain-card " + statusMeta(item.status).cls;
  const rows = (item.metrics || []).map(function (m) {
    const value = metricValueText(m.value);
    const long = value.length > 90;
    return "<tr><td>" + escapeHtml(m.name) + "</td>" +
      "<td" + (long ? " class='cell-long'" : "") + ">" + escapeHtml(value) + "</td>" +
      "<td class='cell-src'>" + escapeHtml(m.source || "") + "</td></tr>";
  }).join("");
  details.innerHTML =
    "<summary>" + statusPill(item.status) + "<strong>" + escapeHtml(item.domain) + "</strong>" +
      (item.message ? "<em>" + escapeHtml(item.message) + "</em>" : "") +
      "<span class='domain-open'>detalhes</span></summary>" +
    (item.message ? "<p class='domain-msg'>" + escapeHtml(item.message) + "</p>" : "") +
    "<div class='domain-table-wrap'><table class='kv-table'><thead><tr><th>Métrica</th><th>Valor</th><th>Fonte</th></tr></thead><tbody>" +
    (rows || "<tr><td colspan='3' class='cell-src'>Sem métricas.</td></tr>") +
    "</tbody></table></div>";
  return details;
}

function renderDomainGrid(elementId, snapshot) {
  const grid = $(elementId);
  if (!grid) return;
  grid.innerHTML = "";
  if (!(snapshot || []).length) {
    grid.className = "domain-grid empty-state";
    grid.textContent = "Faça uma análise para listar as áreas.";
    return;
  }
  grid.className = "domain-grid";
  snapshot.forEach(function (item) { grid.appendChild(domainCard(item)); });
}

function renderHardware() {
  const cpu = metric("CPU", "model") || "CPU não identificada";
  const gpuAdapters = metric("GPU", "adapters") || [];
  const gpu = metric("GPU", "name") || (gpuAdapters[0] && gpuAdapters[0].Name) || "GPU não identificada";
  const ram = metric("RAM", "installed_gb");
  const windows = metric("Windows", "edition") || "Windows";
  const build = metric("Windows", "build");
  const arch = metric("Windows", "architecture");

  $("hardwareHero").className = "hardware-hero";
  $("hardwareHero").innerHTML =
    "<div class='hardware-main'>" +
      "<div class='hardware-chip'><span>CPU</span><strong>" + escapeHtml(cpu) + "</strong></div>" +
      "<div class='hardware-chip'><span>GPU</span><strong>" + escapeHtml(gpu) + "</strong></div>" +
      "<div class='hardware-chip'><span>RAM</span><strong>" + escapeHtml(ram == null ? "—" : ram + " GB") + "</strong></div>" +
      "<div class='hardware-chip'><span>Sistema</span><strong>" + escapeHtml(windows) + "</strong></div>" +
      (build ? "<div class='hardware-chip'><span>Build</span><strong>" + escapeHtml(build) + (arch ? " · " + escapeHtml(arch) : "") + "</strong></div>" : "") +
    "</div>";
}

function applyScan(scan) {
  lastScan = scan;
  const snapshot = scan.snapshot || [];
  const issues = (scan.recommendations || []).filter(function (x) { return x.status === "RECOMMENDED"; }).length;
  const failures = snapshot.filter(function (x) { return x.status === "FAILED"; }).length;

  if (failures) {
    $("overallStatus").textContent = failures + " área(s) falharam na leitura";
  } else if (issues) {
    $("overallStatus").textContent = issues + " recomendação(ões)";
  } else {
    $("overallStatus").textContent = "Tudo medido, nada crítico";
  }
  $("overallTitle").textContent = issues
    ? "Seu PC tem oportunidades de melhoria."
    : "Seu PC não precisa de mudanças agressivas agora.";
  $("overallText").textContent = "Análise #" + escapeHtml(scan.scan_id != null ? scan.scan_id : scan.id) +
    " · " + snapshot.length + " áreas medidas com leituras reais do Windows.";
  $("technicalJson").textContent = JSON.stringify(scan, null, 2);

  renderRecommendations();
  renderHardware();
  renderDomainSummary("homeDomainSummary", snapshot);
  renderDomainSummary("domainSummary", snapshot);
  renderDomainGrid("domainGrid", snapshot);
}

async function scan() {
  const button = $("scanButton");
  button.disabled = true;
  button.textContent = "Analisando...";
  $("overallStatus").textContent = "Analisando o sistema";

  try {
    const response = await zeusAPI.fetch("/api/scan", { cache: "no-store" });
    if (!response.ok) throw new Error("Falha ao analisar o PC.");
    const data = await response.json();
    applyScan(data);
    $("historyList").dataset.loaded = "";
    loadHistoryList();
    toast("Análise concluída.");
  } catch (error) {
    $("overallStatus").textContent = "Falha na análise";
    $("overallText").textContent = error.message;
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Analisar novamente";
  }
}

function pushSeries(name, value) {
  const numeric = Number(value);
  if (value == null || Number.isNaN(numeric)) return;
  series[name].push(numeric);
  if (series[name].length > SERIES_MAX) series[name].shift();
}

function fitCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const cssWidth = canvas.clientWidth || canvas.width;
  const cssHeight = canvas.clientHeight || canvas.height;
  if (canvas.width !== Math.round(cssWidth * dpr)) {
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(cssHeight * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx: ctx, w: cssWidth, h: cssHeight };
}

function drawSeries(canvasId, values, color, maxScale, unit) {
  const canvas = $(canvasId);
  if (!canvas) return;
  const box = fitCanvas(canvas);
  const ctx = box.ctx;
  const w = box.w;
  const h = box.h;
  const padTop = 8;
  const padBottom = 14;
  const plotH = h - padTop - padBottom;
  ctx.clearRect(0, 0, w, h);

  ctx.font = "9px Segoe UI, sans-serif";
  ctx.fillStyle = "rgba(141,152,170,.75)";
  ctx.strokeStyle = "rgba(255,255,255,.055)";
  ctx.lineWidth = 1;
  [0, 50, 100].forEach(function (tick) {
    const y = padTop + plotH * (1 - tick / 100);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
    ctx.fillText(String(Math.round(maxScale * tick / 100)) + (unit || ""), 2, y - 3);
  });

  if (!values || values.length < 2) {
    ctx.fillStyle = "rgba(141,152,170,.75)";
    ctx.fillText("coletando amostras…", 8, padTop + plotH / 2);
    return;
  }

  const stepX = w / (SERIES_MAX - 1);
  const startX = w - (values.length - 1) * stepX;
  const scaleY = function (value) {
    const clamped = Math.max(0, Math.min(maxScale, value));
    return padTop + plotH * (1 - clamped / maxScale);
  };

  const gradient = ctx.createLinearGradient(0, padTop, 0, h);
  gradient.addColorStop(0, color + "33");
  gradient.addColorStop(1, color + "00");

  ctx.beginPath();
  values.forEach(function (value, index) {
    const x = startX + index * stepX;
    const y = scaleY(value);
    if (index) ctx.lineTo(x, y); else ctx.moveTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  ctx.stroke();

  ctx.lineTo(startX + (values.length - 1) * stepX, padTop + plotH);
  ctx.lineTo(startX, padTop + plotH);
  ctx.closePath();
  ctx.fillStyle = gradient;
  ctx.fill();

  const lastX = startX + (values.length - 1) * stepX;
  const lastY = scaleY(values[values.length - 1]);
  ctx.beginPath();
  ctx.arc(lastX, lastY, 3, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();

  const peak = Math.max.apply(null, values);
  ctx.fillStyle = "rgba(245,247,251,.85)";
  ctx.fillText("máx " + Math.round(peak) + (unit || ""), w - 58, padTop + 8);
}

function redrawCharts() {
  drawSeries("cpuChart", series.cpu, CHART_COLORS.cpu, 100, "%");
  drawSeries("gpuChart", series.gpu, CHART_COLORS.gpu, 100, "%");
  drawSeries("ramChart", series.ram, CHART_COLORS.ram, 100, "%");
  drawSeries("tempChart", series.temp, CHART_COLORS.temp, 100, "°");
}

function renderProcessTables(snapshot) {
  const meta = $("processMeta");
  if (meta) {
    const when = snapshot && snapshot.timestamp
      ? new Date(snapshot.timestamp * 1000).toLocaleTimeString("pt-BR")
      : "—";
    meta.textContent = snapshot
      ? snapshot.total_processes + " processos · " + snapshot.sampled_processes + " amostrados · " +
        snapshot.inaccessible_count + " protegidos · " + when
      : "aguardando amostras…";
  }
  renderProcessBox("topCpuBox", snapshot && snapshot.top_cpu, "cpu");
  renderProcessBox("topMemBox", snapshot && snapshot.top_memory, "mem");
}

function renderProcessBox(boxId, points, kind) {
  const box = $(boxId);
  if (!box) return;
  if (!points || !points.length) {
    box.className = "process-box empty-state";
    box.textContent = kind === "cpu"
      ? "Aguardando a segunda amostra para calcular o uso por processo."
      : "Coletando…";
    return;
  }
  box.className = "process-box";
  const rows = points.slice(0, 8).map(function (point) {
    const cpu = point.cpu_pct == null ? "—" : point.cpu_pct.toFixed(1) + "%";
    return "<tr><td class='proc-name'>" + escapeHtml(point.name) + "</td>" +
      "<td class='cell-src'>" + point.pid + "</td>" +
      "<td>" + (kind === "cpu" ? cpu : formatBytes(point.working_set_bytes)) + "</td>" +
      (kind === "cpu" ? "<td class='cell-src'>" + formatBytes(point.working_set_bytes) + "</td>" : "") +
      "</tr>";
  }).join("");
  const head = kind === "cpu"
    ? "<tr><th>Processo</th><th>PID</th><th>CPU</th><th>Memória</th></tr>"
    : "<tr><th>Processo</th><th>PID</th><th>Memória</th></tr>";
  box.innerHTML = "<table class='kv-table proc-table'><thead>" + head + "</thead><tbody>" + rows + "</tbody></table>";
}

async function fetchJson(url) {
  const response = await zeusAPI.fetch(url, { cache: "no-store" });
  const data = await response.json().catch(function () { return null; });
  if (!response.ok) throw new Error((data && (data.error || data.message)) || "Falha na requisição.");
  return data;
}

function applySample(data) {
  const cpu = data.cpu_pct;
  const gpu = data.gpu_pct;
  const ram = data.ram_used_pct;
  const temp = data.gpu_temp_c;

  $("cpuMetric").textContent = cpu == null ? "—" : Math.round(cpu) + "%";
  $("gpuMetric").textContent = gpu == null ? "—" : Math.round(gpu) + "%";
  $("ramMetric").textContent = ram == null ? "—" : Math.round(ram) + "%";
  $("tempMetric").textContent = temp == null ? "—" : Math.round(temp) + "°C";

  setBar("cpuBar", cpu);
  setBar("gpuBar", gpu);
  setBar("ramBar", ram);
  setBar("tempBar", temp);

  $("liveCpu").textContent = cpu == null ? "—" : Math.round(cpu) + "%";
  $("liveGpu").textContent = gpu == null ? "—" : Math.round(gpu) + "%";
  $("liveRam").textContent = ram == null ? "—" : Math.round(ram) + "%";
  $("liveTemp").textContent = temp == null ? "—" : Math.round(temp) + "°C";

  pushSeries("cpu", cpu);
  pushSeries("gpu", gpu);
  pushSeries("ram", ram);
  pushSeries("temp", temp);
  redrawCharts();
}

async function sample() {
  try {
    const data = await fetchJson("/api/observer/sample");
    applySample(data);
  } catch (error) {
    console.debug(error);
  }
}

async function seedHistory() {
  try {
    const data = await fetchJson("/api/observer/history?limit=" + SERIES_MAX);
    (data.samples || []).forEach(function (item) {
      pushSeries("cpu", item.cpu_pct);
      pushSeries("gpu", item.gpu_pct);
      pushSeries("ram", item.ram_used_pct);
      pushSeries("temp", item.gpu_temp_c);
    });
    redrawCharts();
  } catch (error) {
    console.debug(error);
  }
}

async function refreshProcesses() {
  try {
    const data = await fetchJson("/api/observer/processes?limit=8");
    if (data.latest) {
      lastProcessSnapshot = data.latest;
      renderProcessTables(data.latest);
    }
  } catch (error) {
    console.debug(error);
  }
}

function setFrequency(ms) {
  sampleIntervalMs = ms;
  if (sampleTimer) { clearInterval(sampleTimer); sampleTimer = null; }
  if (processTimer) { clearInterval(processTimer); processTimer = null; }
  if (ms > 0) {
    sample();
    sampleTimer = setInterval(sample, ms);
    refreshProcesses();
    processTimer = setInterval(refreshProcesses, Math.max(ms * 4, 5000));
  } else {
    $("processMeta").textContent = "observação pausada";
  }
  document.querySelectorAll(".freq-btn").forEach(function (button) {
    button.classList.toggle("active", Number(button.dataset.freq) === ms);
  });
}

document.querySelectorAll(".freq-btn").forEach(function (button) {
  button.addEventListener("click", function () { setFrequency(Number(button.dataset.freq)); });
});

$("scanButton").addEventListener("click", scan);
$("technicalToggle").addEventListener("click", function () { $("technicalDrawer").classList.add("open"); });
$("closeTechnical").addEventListener("click", function () { $("technicalDrawer").classList.remove("open"); });

function renderPlan(plan) {
  const panel = $("planPanel");
  if (!panel) return;
  const findings = plan.findings || [];
  const summary = plan.summary || {};
  if (!findings.length) {
    panel.className = "plan-panel empty-state";
    panel.textContent = "O motor não encontrou ações aplicáveis neste estado.";
    return;
  }
  panel.className = "plan-panel";

  const flatSummary = {};
  Object.keys(summary).forEach(function (key) {
    const value = summary[key];
    if (value && typeof value === "object") {
      if (key === "status_counts") {
        Object.keys(value).forEach(function (inner) {
          if (value[inner]) flatSummary[inner.toLowerCase().replace(/_/g, " ")] = value[inner];
        });
      }
      return;
    }
    if (key === "apply_recommended_count" || key === "manual_review_count" || key === "blocked_count") return;
    const labels = { finding_count: "achados", automation_eligible_count: "elegíveis p/ automação" };
    flatSummary[labels[key] || key.replace(/_/g, " ")] = value;
  });
  const summaryChips = Object.keys(flatSummary).map(function (key) {
    return "<span class='sum-chip muted'><b>" + escapeHtml(String(flatSummary[key])) + "</b> " +
      escapeHtml(key.replace(/_/g, " ")) + "</span>";
  }).join("");

  const statusClsMap = { APPLY_RECOMMENDED: "ok", MANUAL_REVIEW: "warn", BLOCKED: "bad", NOT_APPLICABLE: "muted", ALREADY_CORRECT: "muted", NO_ACTION: "muted" };
  const cards = findings.map(function (finding) {
    const statusCls = statusClsMap[finding.status] || "muted";
    const evidence = (finding.evidence || []).map(function (item) {
      return "<div class='ev-row'><span>" + escapeHtml((item.domain || "") + " · " + (item.metric || "")) + "</span><strong>" +
        escapeHtml(metricValueText(item.value)) + "</strong></div>";
    }).join("");
    const blockers = (finding.blockers || []).map(function (blocker) {
      return "<span class='blocker-chip'>" + escapeHtml(blocker) + "</span>";
    }).join("");
    return "<details class='plan-card'>" +
      "<summary><span class='state-pill " + statusCls + "'>" + escapeHtml(finding.status) + "</span>" +
        "<strong>" + escapeHtml(finding.title) + "</strong>" +
        "<em>" + escapeHtml(finding.category || "") + "</em></summary>" +
      "<p class='plan-reason'>" + escapeHtml(finding.reason || "") + "</p>" +
      (finding.benefit ? "<p class='plan-benefit'>Benefício: " + escapeHtml(finding.benefit) + "</p>" : "") +
      "<div class='plan-tags'>" +
        "<span class='tag'>confiança " + escapeHtml(finding.confidence || "—") + "</span>" +
        "<span class='tag'>risco " + escapeHtml(finding.risk || "—") + "</span>" +
        "<span class='tag'>" + (finding.reversible ? "reversível" : "não reversível") + "</span>" +
      "</div>" +
      (blockers ? "<div class='plan-blockers'>" + blockers + "</div>" : "") +
      (evidence ? "<div class='evidence'>" + evidence + "</div>" : "") +
    "</details>";
  }).join("");

  panel.innerHTML =
    "<div class='plan-meta'>plano <code>" + escapeHtml(plan.plan_id || "") + "</code> · perfil " +
      escapeHtml(plan.profile || "—") + " · " +
      (plan.execution_ready ? "<span class='state-pill ok'>pronto p/ executar</span>" : "<span class='state-pill warn'>simulação (dry-run)</span>") +
    "</div>" +
    "<div class='domain-summary'>" + summaryChips + "</div>" +
    cards;
}

async function generatePlan() {
  const button = $("planButton");
  const panel = $("planPanel");
  button.disabled = true;
  panel.className = "plan-panel empty-state";
  panel.textContent = "Gerando plano a partir do estado real…";
  try {
    const data = await fetchJson("/api/optimization/plan");
    renderPlan(data.plan || {});
    if (!(data.plan && (data.plan.findings || []).length)) {
      toast("Nenhuma ação aplicável agora.");
    }
  } catch (error) {
    panel.className = "plan-panel empty-state";
    panel.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

$("planButton").addEventListener("click", generatePlan);

function formatStamp(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleString("pt-BR");
}

async function loadHistoryList() {
  const box = $("historyList");
  try {
    const data = await fetchJson("/api/history?limit=25");
    const scans = data.scans || [];
    box.dataset.loaded = "1";
    if (!scans.length) {
      box.className = "history-list empty-state";
      box.textContent = "Nenhuma análise registrada ainda. Rode a primeira análise.";
      return;
    }
    box.className = "history-list";
    box.innerHTML = "<table class='tx-table history-table'><thead><tr>" +
      "<th>#</th><th>Quando</th><th>Perfil</th><th>Áreas com falha</th><th>Recomendações</th><th></th>" +
      "</tr></thead><tbody>" +
      scans.map(function (item) {
        const snapshot = item.snapshot || [];
        const failures = snapshot.filter(function (entry) { return entry.status === "FAILED"; }).length;
        const recommended = (item.recommendations || []).filter(function (entry) { return entry.status === "RECOMMENDED"; }).length;
        return "<tr data-scan='" + item.id + "' class='history-row'>" +
          "<td class='cell-src'>" + item.id + "</td>" +
          "<td>" + escapeHtml(formatStamp(item.created_at)) + "</td>" +
          "<td>" + escapeHtml(item.profile || "—") + "</td>" +
          "<td>" + (failures ? "<span class='state-pill bad'>" + failures + "</span>" : "<span class='state-pill ok'>0</span>") + "</td>" +
          "<td>" + recommended + "</td>" +
          "<td><button class='mini-btn'>Abrir</button></td>" +
        "</tr>";
      }).join("") +
      "</tbody></table>";

    box.querySelectorAll(".history-row").forEach(function (row) {
      row.addEventListener("click", function () { openStoredScan(Number(row.dataset.scan)); });
    });
  } catch (error) {
    box.className = "history-list empty-state";
    box.textContent = "Não foi possível carregar o histórico: " + error.message;
  }
}

async function openStoredScan(scanId) {
  const detail = $("historyDetail");
  const title = $("historyDetailTitle");
  shownHistoryId = scanId;
  title.textContent = "Análise #" + scanId;
  detail.className = "empty-state";
  detail.textContent = "Carregando análise #" + scanId + "…";
  try {
    const scan = await fetchJson("/api/scan/" + scanId);
    detail.className = "";
    const recommended = (scan.recommendations || []).filter(function (x) { return x.status === "RECOMMENDED"; }).length;
    detail.innerHTML =
      "<p class='domain-msg'>Perfil " + escapeHtml(scan.profile || "—") + " · " +
        escapeHtml(formatStamp(scan.created_at)) + " · " + recommended + " recomendação(ões)</p>" +
      "<div id='historyRecs' class='recommendation-list'></div>" +
      "<div class='domain-summary' style='margin:14px 0'></div>" +
      "<div class='domain-grid'></div>";
    const recBox = detail.querySelector("#historyRecs");
    (scan.recommendations || []).forEach(function (item) { recBox.appendChild(recommendationNode(item)); });
    renderDomainSummaryInto(detail.querySelector(".domain-summary"), scan.snapshot || []);
    const grid = detail.querySelector(".domain-grid");
    (scan.snapshot || []).forEach(function (item) { grid.appendChild(domainCard(item)); });
  } catch (error) {
    detail.className = "empty-state";
    detail.textContent = "Falha ao carregar a análise: " + error.message;
  }
}

function renderDomainSummaryInto(box, snapshot) {
  const counts = domainCounts(snapshot);
  box.className = "domain-summary";
  box.innerHTML =
    "<span class='sum-chip ok'><b>" + counts.ok + "</b> OK</span>" +
    "<span class='sum-chip warn'><b>" + counts.warn + "</b> parciais</span>" +
    "<span class='sum-chip muted'><b>" + counts.muted + "</b> sem leitura</span>" +
    "<span class='sum-chip bad'><b>" + counts.bad + "</b> falhas</span>";
}

/* ---------- Energia: transação com consentimento ---------- */

function powerApi(url, payload) {
  const options = payload === undefined
    ? { cache: "no-store" }
    : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      };
  return zeusAPI.fetch(url, options).then(function (response) {
    return response.json().then(
      function (data) { return { ok: response.ok, status: response.status, data: data }; },
      function () { return { ok: response.ok, status: response.status, data: null }; }
    );
  });
}

function planNameForGuid(guid) {
  const needle = String(guid || "").toLowerCase();
  const match = powerPlans.find(function (plan) { return plan.guid === needle; });
  return match ? match.name : String(guid || "—");
}

function activePlanGuid() {
  const match = powerPlans.find(function (plan) { return plan.active; });
  return match ? match.guid : null;
}

function stopConsentCountdown() {
  if (powerCountdownTimer) {
    clearInterval(powerCountdownTimer);
    powerCountdownTimer = null;
  }
}

function tickConsentCountdown() {
  const el = $("consentCountdown");
  if (!el || !powerPending) return;
  const remaining = Math.floor((Date.parse(powerPending.expires_at) - Date.now()) / 1000);
  if (Number.isNaN(remaining)) { el.textContent = "—"; return; }
  if (remaining <= 0) {
    el.textContent = "expirado";
    stopConsentCountdown();
    const confirm = $("consentConfirm");
    if (confirm) confirm.disabled = true;
    const note = $("consentNote");
    if (note) note.textContent = "O consentimento expirou. Cancele e prepare novamente.";
    return;
  }
  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  el.textContent = minutes + "m " + (seconds < 10 ? "0" : "") + seconds + "s";
}

function startConsentCountdown() {
  stopConsentCountdown();
  tickConsentCountdown();
  powerCountdownTimer = setInterval(tickConsentCountdown, 1000);
}

function setPowerStage(html) {
  const stage = $("powerStage");
  if (stage) stage.innerHTML = html;
}

function renderStageStatus(text) {
  setPowerStage("<div class='stage-card stage-status'><strong>" + escapeHtml(text) + "</strong></div>");
}

function renderStagePending(text) {
  setPowerStage(
    "<div class='stage-card stage-pending'><div class='stage-spinner'></div>" +
    "<strong>" + escapeHtml(text) + "</strong>" +
    "<p>Aguardando o resultado real do servidor. Nenhum sucesso aparece antes da verificacao independente.</p></div>"
  );
}

function renderStageError(text) {
  setPowerStage(
    "<div class='stage-card stage-error'><span class='eyebrow'>FALHA</span>" +
    "<strong>" + escapeHtml(text) + "</strong>" +
    "<p>Revise o historico de transacoes abaixo. Nada foi confirmado como aplicado.</p></div>"
  );
}

function statePill(state) {
  const map = {
    VERIFIED: "ok", PREPARED: "pending", CONSENTED: "pending", APPLYING: "pending", APPLIED: "pending",
    ROLLING_BACK: "warn", ROLLED_BACK: "warn", FAILED: "bad", EXPIRED: "muted", CANCELLED: "muted",
  };
  const cls = map[state] || "muted";
  return "<span class='state-pill " + cls + "'>" + escapeHtml(state || "—") + "</span>";
}

function powerPanelSkeleton() {
  return "" +
    "<div class='power-head'>" +
      "<div><span class='eyebrow'>ENERGIA</span><h3>Plano de energia com consentimento</h3></div>" +
      "<button class='mini-btn' id='powerRefresh'>Atualizar</button>" +
    "</div>" +
    "<div id='powerPlans' class='plan-list'><div class='power-loading'>Carregando planos...</div></div>" +
    "<div id='powerStage'></div>" +
    "<div class='power-history-head'>" +
      "<div><span class='eyebrow'>HISTORICO</span><h3>Transacoes registradas</h3></div>" +
      "<button class='mini-btn' id='powerHistoryRefresh'>Atualizar</button>" +
    "</div>" +
    "<div id='powerHistory' class='tx-scroll'></div>";
}

function renderPlans() {
  const box = $("powerPlans");
  if (!box) return;
  if (!powerPlans.length) { box.innerHTML = "<div class='power-empty'>Nenhum plano encontrado.</div>"; return; }
  box.innerHTML = powerPlans.map(function (plan) {
    const action = plan.active
      ? "<span class='plan-active-tag'>Em uso</span>"
      : "<button class='apply-btn' data-guid='" + escapeHtml(plan.guid) + "'>Aplicar</button>";
    return "<div class='plan-row'>" +
      "<div class='plan-info'><strong>" + escapeHtml(plan.name) + "</strong>" +
        "<code>" + escapeHtml(plan.guid) + "</code></div>" +
      "<div class='plan-action'>" + action + "</div>" +
    "</div>";
  }).join("");
}

function renderPlansError(text) {
  const box = $("powerPlans");
  if (box) box.innerHTML = "<div class='power-empty'>" + escapeHtml(text) + "</div>";
}

async function loadPlans() {
  const box = $("powerPlans");
  if (box) box.innerHTML = "<div class='power-loading'>Carregando planos...</div>";
  try {
    const res = await powerApi("/api/power-plans");
    if (!res.ok || !res.data) {
      powerPlans = [];
      renderPlansError((res.data && (res.data.error || res.data.message)) || "Falha ao carregar os planos.");
      return;
    }
    powerPlans = (res.data.plans || []).map(function (plan) {
      return { guid: String(plan.guid || "").toLowerCase(), name: plan.name, active: !!plan.active };
    });
    renderPlans();
  } catch (error) {
    powerPlans = [];
    renderPlansError("Sem conexao com o servidor local.");
  }
}

function renderHistory() {
  const box = $("powerHistory");
  if (!box) return;
  if (!powerTxCache.length) { box.innerHTML = "<div class='power-empty'>Nenhuma transacao registrada ainda.</div>"; return; }
  const rows = powerTxCache.map(function (tx) {
    const target = planNameForGuid(tx.args && tx.args.guid);
    const err = tx.error_text
      ? "<span class='tx-error'>" + escapeHtml(tx.error_text) + "</span>"
      : "<span class='tx-muted'>—</span>";
    return "<tr>" +
      "<td>" + statePill(tx.state) + "</td>" +
      "<td class='tx-action'>" + escapeHtml(tx.action_id) + "</td>" +
      "<td class='tx-target'>" + escapeHtml(target) + "</td>" +
      "<td class='tx-time'>" + escapeHtml(formatStamp(tx.created_at)) + "</td>" +
      "<td class='tx-time'>" + escapeHtml(formatStamp(tx.updated_at)) + "</td>" +
      "<td>" + err + "</td>" +
    "</tr>";
  }).join("");
  box.innerHTML = "<table class='tx-table'><thead><tr>" +
    "<th>Estado</th><th>Acao</th><th>Plano alvo</th><th>Criada</th><th>Atualizada</th><th>Erro</th>" +
    "</tr></thead><tbody>" + rows + "</tbody></table>";
}

async function loadHistory() {
  const box = $("powerHistory");
  if (!box) return;
  box.innerHTML = "<div class='power-loading'>Carregando historico...</div>";
  try {
    const res = await powerApi("/api/transactions?limit=20");
    if (!res.ok || !res.data) {
      box.innerHTML = "<div class='power-empty'>" + escapeHtml((res.data && (res.data.error || res.data.message)) || "Falha ao carregar o historico.") + "</div>";
      return;
    }
    powerTxCache = res.data.transactions || [];
    renderHistory();
  } catch (error) {
    box.innerHTML = "<div class='power-empty'>Sem conexao com o servidor local.</div>";
  }
}

async function loadPlansAndHistory() {
  await loadPlans();
  await loadHistory();
}

function onPlanActionClick(event) {
  const button = event.target.closest("button.apply-btn");
  if (!button) return;
  const guid = button.dataset.guid;
  const plan = powerPlans.find(function (item) { return item.guid === guid; });
  preparePowerChange(guid, plan ? plan.name : guid);
}

function bindPowerPanel() {
  const refreshAll = $("powerRefresh");
  if (refreshAll) refreshAll.addEventListener("click", loadPlansAndHistory);
  const refreshHistory = $("powerHistoryRefresh");
  if (refreshHistory) refreshHistory.addEventListener("click", loadHistory);
  const box = $("powerPlans");
  if (box) box.addEventListener("click", onPlanActionClick);
}

function openPowerPanel() {
  const panel = $("morePanel");
  stopConsentCountdown();
  powerPending = null;
  panel.className = "detail-panel power-panel";
  panel.innerHTML = powerPanelSkeleton();
  bindPowerPanel();
  loadPlansAndHistory();
}

async function preparePowerChange(guid, name) {
  if (powerBusy) return;
  if (guid === activePlanGuid()) { toast("Este plano ja esta ativo."); return; }
  powerBusy = true;
  renderStageStatus("Preparando a alteracao. Nada foi aplicado ainda.");
  try {
    const res = await powerApi("/api/transactions/prepare", { action_id: "SET_ACTIVE_POWER_PLAN", args: { guid: guid } });
    if (!res.ok || !res.data || !res.data.transaction) {
      const msg = (res.data && (res.data.message || res.data.error)) || "Nao foi possivel preparar a alteracao.";
      renderStageError(msg);
      await loadHistory();
      return;
    }
    const tx = res.data.transaction;
    powerPending = {
      transaction_id: tx.transaction_id,
      consent_token: tx.consent_token,
      expires_at: tx.consent_expires_at,
      args: tx.args,
      before_state: tx.before_state,
      targetName: name,
    };
    if (!tx.execution_enabled) {
      renderStageError("Esta acao nao possui executor habilitado no servidor.");
    } else {
      renderConsentCard();
    }
    await loadHistory();
  } catch (error) {
    renderStageError("Falha de conexao ao preparar a alteracao.");
  } finally {
    powerBusy = false;
  }
}

function renderConsentCard() {
  if (!powerPending) return;
  const before = powerPending.before_state || {};
  const currentName = before.active_name || planNameForGuid(before.active_guid);
  const targetGuid = (powerPending.args && powerPending.args.guid) || "";
  const targetName = powerPending.targetName || planNameForGuid(targetGuid);
  setPowerStage(
    "<div class='consent-card'>" +
      "<div class='consent-head'><span class='eyebrow'>CONSENTIMENTO</span><h4>Confirme a mudanca de plano de energia</h4></div>" +
      "<div class='consent-body'>" +
        "<div class='consent-line'><span>Voce vai aplicar</span><strong>" + escapeHtml(targetName) + "</strong><code>" + escapeHtml(targetGuid) + "</code></div>" +
        "<div class='consent-line'><span>Plano atual (o que um rollback restaura)</span><strong>" + escapeHtml(currentName) + "</strong></div>" +
        "<div class='consent-line'><span>Validade deste consentimento</span><strong id='consentCountdown'>--</strong></div>" +
      "</div>" +
      "<label class='consent-check'><input type='checkbox' id='consentAck'> <span>Entendo que isto altera o plano de energia ativo neste PC e autorizo o Zeus a aplicar e verificar.</span></label>" +
      "<p class='consent-note' id='consentNote'>O Zeus prepara a acao, espera seu consentimento explicito, aplica e depois confirma por leitura independente. Nada muda sem este passo, e um eventual rollback devolve o plano atual listado acima.</p>" +
      "<div class='consent-actions'>" +
        "<button class='confirm-btn' id='consentConfirm' disabled>Confirmar e aplicar</button>" +
        "<button class='cancel-btn' id='consentCancel'>Cancelar</button>" +
      "</div>" +
    "</div>"
  );
  const ack = $("consentAck");
  const confirm = $("consentConfirm");
  const cancel = $("consentCancel");
  if (ack && confirm) ack.addEventListener("change", function () { confirm.disabled = !ack.checked; });
  if (cancel) cancel.addEventListener("click", function () {
    stopConsentCountdown();
    powerPending = null;
    renderStageStatus("Alteracao cancelada. Nenhuma mudanca foi aplicada.");
    loadHistory();
  });
  if (confirm) confirm.addEventListener("click", executeConsentFlow);
  startConsentCountdown();
}

async function executeConsentFlow() {
  if (!powerPending || powerBusy) return;
  const ack = $("consentAck");
  if (!ack || !ack.checked) return;
  powerBusy = true;
  const transactionId = powerPending.transaction_id;
  const token = powerPending.consent_token;
  renderStagePending("Aplicando e verificando...");
  try {
    const consentRes = await powerApi("/api/transactions/consent", { transaction_id: transactionId, consent_token: token });
    if (!consentRes.ok || !consentRes.data || !consentRes.data.transaction) {
      const msg = (consentRes.data && (consentRes.data.message || consentRes.data.error)) || "Consentimento invalido ou expirado.";
      renderStageError(msg);
      return;
    }
    if (consentRes.data.transaction.state !== "CONSENTED") {
      renderStageError("O servidor nao registrou o consentimento desta transacao.");
      return;
    }
    const execRes = await powerApi("/api/transactions/execute", { transaction_id: transactionId });
    renderExecuteOutcome(execRes);
  } catch (error) {
    renderStageError("Falha de conexao durante a aplicacao. Confira o historico antes de tentar novamente.");
  } finally {
    powerBusy = false;
    stopConsentCountdown();
    powerPending = null;
    await loadHistory();
    await loadPlans();
  }
}

function renderExecuteOutcome(res) {
  const data = (res && res.data) || {};
  const tx = data.transaction;
  if (!tx) {
    renderStageError(data.message || data.error || "A execucao foi recusada pelo servidor.");
    return;
  }
  const state = tx.state;
  if (state === "VERIFIED") { renderVerifiedOutcome(tx); return; }
  if (state === "ROLLED_BACK") { renderRolledBackOutcome(tx); return; }
  if (state === "FAILED") { renderFailedOutcome(tx); return; }
  renderStageError((tx.message || data.message) || "A transacao terminou em estado inesperado: " + String(state));
}

function renderVerifiedOutcome(tx) {
  const verify = tx.verify || {};
  const apply = tx.apply || {};
  const expected = verify.expected_guid || (tx.args && tx.args.guid) || "—";
  const actual = verify.actual_guid || "—";
  const source = verify.source || "—";
  setPowerStage(
    "<div class='stage-card stage-verified'>" +
      "<span class='eyebrow'>CONFIRMADO</span>" +
      "<strong>" + escapeHtml(tx.message || "Acao aplicada e verificada por leitura independente.") + "</strong>" +
      "<div class='evidence'>" +
        "<div class='ev-row'><span>Plano aplicado</span><strong>" + escapeHtml(planNameForGuid(tx.args && tx.args.guid)) + "</strong></div>" +
        "<div class='ev-row'><span>GUID esperado</span><code>" + escapeHtml(expected) + "</code></div>" +
        "<div class='ev-row'><span>GUID observado</span><code>" + escapeHtml(actual) + "</code></div>" +
        "<div class='ev-row'><span>Fonte da verificacao</span><strong>" + escapeHtml(source) + "</strong></div>" +
      "</div>" +
      (apply.command ? "<p class='ev-cmd'>Comando executado: " + escapeHtml(apply.command) + "</p>" : "") +
    "</div>"
  );
}

function renderRolledBackOutcome(tx) {
  const verify = tx.verify || {};
  setPowerStage(
    "<div class='stage-card stage-warn'>" +
      "<span class='eyebrow'>REVERTIDO</span>" +
      "<strong>A verificacao falhou e o plano anterior foi restaurado.</strong>" +
      (tx.message ? "<p>" + escapeHtml(tx.message) + "</p>" : "") +
      "<div class='evidence'>" +
        "<div class='ev-row'><span>GUID esperado</span><code>" + escapeHtml(verify.expected_guid || "—") + "</code></div>" +
        "<div class='ev-row'><span>GUID observado</span><code>" + escapeHtml(verify.actual_guid || "—") + "</code></div>" +
      "</div>" +
    "</div>"
  );
}

function renderFailedOutcome(tx) {
  const detail = (tx.apply && tx.apply.message) || tx.error_text || tx.message || "Nao foi possivel concluir a alteracao.";
  const head = tx.resource_locked
    ? "Nao foi possivel confirmar; o recurso ficou travado."
    : "Nao foi possivel confirmar a alteracao.";
  setPowerStage(
    "<div class='stage-card stage-failed'>" +
      "<span class='eyebrow'>FALHA</span>" +
      "<strong>" + escapeHtml(head) + "</strong>" +
      "<p>" + escapeHtml(detail) + "</p>" +
      (tx.resource_locked ? "<p class='locked-note'>O recurso de energia fica bloqueado ate recuperacao manual. Revise o historico antes de nova tentativa.</p>" : "") +
    "</div>"
  );
}

document.querySelectorAll(".more-tile").forEach(function (tile) {
  tile.addEventListener("click", async function () {
    const type = tile.dataset.more;
    const panel = $("morePanel");

    if (type === "power") {
      openPowerPanel();
      return;
    }

    if (type === "report") {
      panel.className = "detail-panel";
      panel.innerHTML =
        "<div class='stage-card stage-status'><strong>Relatório de diagnóstico</strong>" +
        "<p>O relatório é gerado pelo servidor local a partir da análise mais recente registrada, em HTML independente, e baixado direto. Nada é enviado para fora do seu PC.</p>" +
        "<p style='margin-top:12px'><a class='primary-btn small' style='display:inline-block;text-decoration:none' href='/api/report' download>Baixar relatório</a></p></div>";
      return;
    }

    stopConsentCountdown();
    powerPending = null;
    panel.className = "detail-panel";

    if (!lastScan) {
      panel.textContent = "Faça uma análise primeiro.";
      return;
    }

    const domainMap = { updates: "Windows Update", startup: "Startup", security: "Security" };
    const item = lastScan.snapshot.find(function (entry) { return entry.domain === domainMap[type]; });
    if (!item) { panel.textContent = "Sem dados."; return; }
    panel.innerHTML = "";
    const card = domainCard(item);
    card.open = true;
    panel.appendChild(card);
  });
});

window.addEventListener("resize", redrawCharts);

document.addEventListener("click", async function (event) {
  const link = event.target.closest("a[download]");
  if (!link) return;
  const url = new URL(link.href, location.origin);
  if (url.origin !== location.origin || url.pathname !== "/api/report") return;
  event.preventDefault();
  try {
    const response = await zeusAPI.fetch(url.href);
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.message || error.error || "Não foi possível exportar o relatório.");
    }
    const blobUrl = URL.createObjectURL(await response.blob());
    const download = document.createElement("a");
    download.href = blobUrl;
    const disposition = response.headers.get("Content-Disposition") || "";
    const filename = disposition.match(/filename=(zeus-pc-relatorio-[a-z0-9-]+\.html)/i);
    download.download = filename ? filename[1] : "zeus-pc-relatorio.html";
    document.body.appendChild(download);
    download.click();
    download.remove();
    setTimeout(function () { URL.revokeObjectURL(blobUrl); }, 30000);
  } catch (error) {
    toast(error.message || "Falha ao exportar relatório.");
  }
});

async function restoreLatestScan() {
  try {
    const data = await fetchJson("/api/history?limit=1");
    const latest = (data.scans || [])[0];
    if (!latest) return;
    latest.scan_id = latest.id;
    applyScan(latest);
  } catch (error) {
    console.debug(error);
  }
}

(async function init() {
  try {
    const response = await zeusAPI.fetch("/api/status", { cache: "no-store" });
    const status = await response.json();
    $("buildText").textContent = "v" + status.version + " · " + status.build;
  } catch (error) {
    $("buildText").textContent = "Offline";
  }
  await seedHistory();
  setFrequency(1000);
  refreshProcesses();
  restoreLatestScan();
})();
