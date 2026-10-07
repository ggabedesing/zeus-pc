const $ = (id) => document.getElementById(id);

let lastScan = null;
let observerTimer = null;
const series = { cpu: [], gpu: [], ram: [] };

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char];
  });
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
  const numeric = Number(value || 0);
  const pct = Math.max(0, Math.min(100, numeric / max * 100));
  $(id).style.width = pct + "%";
}

function recommendationNode(item) {
  const div = document.createElement("div");
  div.className = "recommendation";
  div.innerHTML =
    "<div><h4>" + escapeHtml(item.title) + "</h4><p>" + escapeHtml(item.reason) +
    "</p></div><span class='tag'>" + escapeHtml(item.status) + "</span>";
  return div;
}

function renderRecommendations() {
  const list = lastScan && lastScan.recommendations ? lastScan.recommendations : [];
  const home = $("homeRecommendations");
  const full = $("optimizationList");
  home.innerHTML = "";
  full.innerHTML = "";

  list.slice(0, 2).forEach(function (item) { home.appendChild(recommendationNode(item)); });
  list.forEach(function (item) { full.appendChild(recommendationNode(item)); });

  $("recommendationCount").textContent = list.filter(function (x) { return x.status === "RECOMMENDED"; }).length;
  $("blockedCount").textContent = list.filter(function (x) { return x.status === "BLOCKED"; }).length;
  $("okCount").textContent = list.filter(function (x) { return x.status === "ALREADY_CORRECT"; }).length;
}

function renderHardware() {
  const cpu = metric("CPU", "model") || "CPU não identificada";
  const gpuAdapters = metric("GPU", "adapters") || [];
  const gpu = metric("GPU", "name") || (gpuAdapters[0] && gpuAdapters[0].Name) || "GPU não identificada";
  const ram = metric("RAM", "installed_gb");
  const windows = metric("Windows", "edition") || "Windows";

  $("hardwareHero").className = "hardware-hero";
  $("hardwareHero").innerHTML =
    "<div class='hardware-main'>" +
      "<div class='hardware-chip'><span>CPU</span><strong>" + escapeHtml(cpu) + "</strong></div>" +
      "<div class='hardware-chip'><span>GPU</span><strong>" + escapeHtml(gpu) + "</strong></div>" +
      "<div class='hardware-chip'><span>RAM</span><strong>" + escapeHtml(ram == null ? "—" : ram + " GB") + "</strong></div>" +
      "<div class='hardware-chip'><span>Sistema</span><strong>" + escapeHtml(windows) + "</strong></div>" +
    "</div>";

  const list = $("hardwareList");
  list.innerHTML = "";
  lastScan.snapshot.forEach(function (item) {
    const details = document.createElement("details");
    details.className = "hardware-detail";
    details.innerHTML =
      "<summary>" + escapeHtml(item.domain) + " · " + escapeHtml(item.status) + "</summary>" +
      "<pre>" + escapeHtml(JSON.stringify(item, null, 2)) + "</pre>";
    list.appendChild(details);
  });
}

async function scan() {
  const button = $("scanButton");
  button.disabled = true;
  button.textContent = "Analisando...";
  $("overallStatus").textContent = "Analisando o sistema";

  try {
    const response = await fetch("/api/scan", { cache: "no-store" });
    if (!response.ok) throw new Error("Falha ao analisar o PC.");
    lastScan = await response.json();

    const issues = lastScan.recommendations.filter(function (x) { return x.status === "RECOMMENDED"; }).length;
    $("overallStatus").textContent = issues ? issues + " recomendação(ões)" : "Nenhuma ação crítica";
    $("overallTitle").textContent = issues ? "Seu PC tem oportunidades de melhoria." : "Seu PC não precisa de mudanças agressivas agora.";
    $("overallText").textContent = "O Zeus analisou hardware, Windows, energia, segurança, inicialização e armazenamento.";
    $("technicalJson").textContent = JSON.stringify(lastScan, null, 2);

    renderRecommendations();
    renderHardware();
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
  if (value == null || Number.isNaN(Number(value))) return;
  series[name].push(Number(value));
  if (series[name].length > 60) series[name].shift();
}

function drawChart(canvasId, values) {
  const canvas = $(canvasId);
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  if (values.length < 2) return;
  ctx.strokeStyle = "#7dff8d";
  ctx.lineWidth = 3;
  ctx.beginPath();
  values.forEach(function (value, index) {
    const x = index / Math.max(1, values.length - 1) * w;
    const y = h - Math.max(0, Math.min(100, value)) / 100 * (h - 10) - 5;
    if (index) ctx.lineTo(x, y); else ctx.moveTo(x, y);
  });
  ctx.stroke();
}

async function sample() {
  try {
    const response = await fetch("/api/observer/sample", { cache: "no-store" });
    const data = await response.json();
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
    $("temperatureFill").style.width = Math.max(0, Math.min(100, Number(temp || 0))) + "%";

    pushSeries("cpu", cpu);
    pushSeries("gpu", gpu);
    pushSeries("ram", ram);
    drawChart("cpuChart", series.cpu);
    drawChart("gpuChart", series.gpu);
    drawChart("ramChart", series.ram);
  } catch (error) {
    console.debug(error);
  }
}

$("scanButton").addEventListener("click", scan);
$("technicalToggle").addEventListener("click", function () { $("technicalDrawer").classList.add("open"); });
$("closeTechnical").addEventListener("click", function () { $("technicalDrawer").classList.remove("open"); });

$("observerToggle").addEventListener("click", function () {
  if (observerTimer) {
    clearInterval(observerTimer);
    observerTimer = null;
    $("observerToggle").textContent = "Iniciar observação";
    return;
  }
  sample();
  observerTimer = setInterval(sample, 1000);
  $("observerToggle").textContent = "Parar observação";
});

document.querySelectorAll(".more-tile").forEach(function (tile) {
  tile.addEventListener("click", async function () {
    const type = tile.dataset.more;
    const panel = $("morePanel");

    if (type === "power") {
      const response = await fetch("/api/power-plans");
      const data = await response.json();
      const rows = (data.plans || []).map(function (plan) {
        return "<div style='padding:9px 0;border-bottom:1px solid rgba(255,255,255,.06)'>" +
          escapeHtml(plan.name) + (plan.active ? " · ativo" : "") + "</div>";
      }).join("");
      panel.innerHTML = "<strong>Planos de energia</strong><div style='margin-top:12px'>" + (rows || "Nenhum plano encontrado.") + "</div>";
      return;
    }

    if (!lastScan) {
      panel.textContent = "Faça uma análise primeiro.";
      return;
    }

    const domainMap = { updates: "Windows Update", startup: "Startup", security: "Security" };
    const item = lastScan.snapshot.find(function (entry) { return entry.domain === domainMap[type]; });
    panel.innerHTML = item
      ? "<pre style='white-space:pre-wrap'>" + escapeHtml(JSON.stringify(item, null, 2)) + "</pre>"
      : "Sem dados.";
  });
});

(async function init() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    const status = await response.json();
    $("buildText").textContent = "v" + status.version + " · " + status.build;
  } catch (error) {
    $("buildText").textContent = "Offline";
  }
  sample();
})();
