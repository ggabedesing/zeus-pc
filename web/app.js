const $ = (id) => document.getElementById(id);

let lastScan = null;
let lastPlan = null;
let observerTimer = null;
const series = { cpu: [], gpu: [], ram: [] };

const DOMAIN_GROUPS = [
  {
    title: "Sistema",
    domains: ["Windows", "Power", "BIOS", "Motherboard", "Integrity", "Virtualization", "Windows Features"],
  },
  {
    title: "Hardware",
    domains: ["CPU", "RAM", "GPU", "Storage", "Physical Storage", "Storage Reliability", "TRIM"],
  },
  {
    title: "Segurança",
    domains: ["Security", "Firewall", "Secure Boot", "TPM", "Windows Events"],
  },
  {
    title: "Rede",
    domains: ["Network", "Network Link", "Proxy"],
  },
  {
    title: "Software e inicialização",
    domains: ["Startup", "Installed Software", "Windows Update", "Drivers", "Devices"],
  },
  {
    title: "Serviços e processos",
    domains: ["Services", "Scheduled Tasks", "Processes"],
  },
];

const METRIC_LABELS = {
  system: "Sistema",
  architecture: "Arquitetura",
  edition: "Edição",
  version: "Versão",
  build: "Build",
  last_boot: "Última inicialização",
  model: "Modelo",
  cores: "Núcleos",
  threads: "Threads",
  max_clock_mhz: "Clock máximo",
  load_pct: "Uso atual",
  installed_gb: "Memória instalada",
  usable_gb: "Memória utilizável",
  available_gb: "Memória disponível",
  used_pct: "Memória em uso",
  modules: "Módulos de memória",
  name: "Nome",
  memory_total_mib: "Memória total",
  memory_used_mib: "Memória usada",
  utilization_pct: "Uso",
  temperature_c: "Temperatura",
  graphics_clock_mhz: "Clock",
  driver: "Driver",
  volumes: "Volumes",
  disk_count: "Discos",
  unhealthy_count: "Com problema",
  disks: "Discos",
  ntfs_enabled: "TRIM no NTFS",
  refs_enabled: "TRIM no ReFS",
  manufacturer: "Fabricante",
  product: "Produto",
  release_date: "Data de lançamento",
  active_scheme: "Plano de energia ativo",
  active_guid: "GUID do plano",
  antivirus_enabled: "Antivírus",
  realtime_enabled: "Tempo real",
  behavior_monitor_enabled: "Monitoramento de comportamento",
  profile_count: "Perfis",
  enabled_count: "Perfis ativos",
  all_profiles_enabled: "Todos os perfis ativos",
  items: "Itens",
  pending_count: "Atualizações pendentes",
  count: "Quantidade",
  unsigned_count: "Sem assinatura",
  problem_count: "Dispositivos com problema",
  problems: "Problemas",
  adapter_count: "Adaptadores",
  up_count: "Ativos",
  running_count: "Em execução",
  automatic_count: "Automáticos",
  total_working_set_bytes: "Memória dos processos",
  top_memory: "Maior consumo de memória",
  publisher_count: "Fabricantes",
  hypervisor_present: "Hipervisor presente",
  firmware_virtualization_enabled: "Virtualização na firmware",
  slat_supported: "SLAT suportado",
  vbs_status: "Isolamento de núcleo (VBS)",
  window_hours: "Janela analisada",
  event_count: "Eventos",
  critical_count: "Críticos",
  error_count: "Erros",
  top_providers: "Principais fontes",
  pending_reboot: "Reinicialização pendente",
  sfc_checked: "SFC verificado",
  dism_checked: "DISM verificado",
  enabled: "Ativado",
  server: "Servidor",
  scope: "Escopo",
  auto_detect: "Detecção automática",
  auto_config_url: "URL de configuração",
  present: "Presente",
  ready: "Pronto",
  activated: "Ativado",
  auto_provisioning: "Provisionamento automático",
  details: "Detalhes",
  profiles: "Perfis",
  states: "Estados",
  events: "Eventos",
  adapters: "Adaptadores",
};

const STATUS_LABELS = {
  OK: "OK",
  PARTIAL: "Parcial",
  UNSUPPORTED: "Não suportado",
  REQUIRES_ELEVATION: "Precisa de administrador",
  NOT_CHECKED: "Não verificado",
  FAILED: "Falhou",
};

const FINDING_LABELS = {
  APPLY_RECOMMENDED: "Pronto para aplicar",
  MANUAL_REVIEW: "Requer revisão",
  BLOCKED: "Bloqueado",
  ALREADY_CORRECT: "Já está correto",
  NOT_APPLICABLE: "Não se aplica",
  NO_ACTION: "Nenhuma ação",
};

const BLOCKER_LABELS = {
  NO_ALLOWLISTED_ACTION: "não existe ação permitida no catálogo",
  NOT_REVERSIBLE: "a ação não é reversível",
  CONFIDENCE_NOT_HIGH: "a evidência não é conclusiva o bastante",
  INCOMPLETE_CORE_DIAGNOSTICS: "o diagnóstico essencial está incompleto",
  NO_SAFE_AUTOMATIC_ACTION: "nenhuma ação automática é segura aqui",
  MEASURE_FRAMETIME_BEFORE_ACTION: "é preciso medir frametime antes de agir",
  PROFILE_REQUIRES_GAMING: "exige perfil gaming",
  REQUIRES_ELEVATION: "exige privilégio de administrador",
  PRECONDITION_FAILED: "pré-condição não confirmada",
  ACTION_NOT_REVERSIBLE: "a ação não é reversível",
  LOW_CONFIDENCE: "confiança baixa na evidência",
  RESOURCE_BUSY: "outro processo está usando o recurso",
  DOMAIN_NOT_AVAILABLE: "o domínio do Windows não respondeu",
};

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char];
  });
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  setTimeout(function () { el.classList.remove("show"); }, 2600);
}

function go(page) {
  document.querySelectorAll(".page").forEach(function (el) { el.classList.remove("active"); });
  document.querySelectorAll(".nav-item").forEach(function (el) {
    el.classList.toggle("active", el.dataset.page === page);
  });
  const target = $("page-" + page);
  if (target) target.classList.add("active");
  if (page === "optimize") renderPlan();
  if (page === "performance") refreshProcesses();
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

/* ---------- formatação legível ---------- */

function formatDate(value) {
  const text = String(value == null ? "" : value);
  const match = text.match(/\/Date\((\d+)\)\//);
  const ms = match ? Number(match[1]) : Number.NaN;
  if (Number.isNaN(ms)) return text || "—";
  const date = new Date(ms);
  return date.toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function formatBytes(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n)) return null;
  const gb = n / (1024 * 1024 * 1024);
  if (gb >= 1) return gb.toFixed(1).replace(".", ",") + " GB";
  return (n / (1024 * 1024)).toFixed(0) + " MB";
}

function describeItem(item) {
  if (item == null) return "";
  if (typeof item !== "object") return String(item);
  const name = item.DisplayName || item.Name || item.TaskName || item.FriendlyName || item.DeviceName || item.provider;
  const bits = [];
  if (name) bits.push(String(name));
  if (item.Publisher) bits.push(String(item.Publisher));
  if (item.Speed) bits.push(String(item.Speed) + " MHz");
  if (item.Capacity != null) {
    const cap = formatBytes(item.Capacity);
    if (cap) bits.push(cap);
  }
  if (item.Enabled === 1 || item.Enabled === true) bits.push("ativo");
  if (item.Enabled === 0 || item.Enabled === false) bits.push("inativo");
  if (item.count != null && !name) bits.push(item.count + " ocorrências");
  if (!bits.length) return Object.keys(item).slice(0, 3).map(function (key) {
    return key + ": " + String(item[key]);
  }).join(" · ");
  return bits.join(" · ");
}

function formatValue(name, value, unit) {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "Sim" : "Não";
  if (Array.isArray(value)) {
    if (!value.length) return "Nenhum";
    const described = value.slice(0, 4).map(describeItem).filter(Boolean);
    const extra = value.length > 4 ? " +" + (value.length - 4) + " outros" : "";
    return described.join("; ") + extra;
  }
  if (typeof value === "object") {
    if (/^\/Date\(/.test(String(value))) return formatDate(value);
    const parts = Object.keys(value).slice(0, 4).map(function (key) {
      const inner = value[key];
      return key + ": " + (inner == null ? "—" : String(inner));
    });
    return parts.join(" · ") || "—";
  }
  if (/^\/Date\(/.test(String(value))) return formatDate(value);
  let text = String(value);
  if (name === "active_scheme") {
    const scheme = text.match(/\(([^)]+)\)/);
    if (scheme) return scheme[1];
  }
  if (unit) text = text + " " + unit;
  if (name.endsWith("_bytes")) {
    const human = formatBytes(value);
    if (human) text = human;
  }
  return text;
}

/* ---------- recomendações e plano ---------- */

function recommendationNode(item) {
  const div = document.createElement("div");
  div.className = "row";
  const status = String(item.status || "").toLowerCase();
  div.innerHTML =
    "<div class='row-main'><h4>" + escapeHtml(item.title) + "</h4><p>" +
    escapeHtml(item.reason) + "</p></div>" +
    "<span class='tag tag-" + escapeHtml(status) + "'>" + escapeHtml(item.status) + "</span>";
  return div;
}

function renderRecommendations() {
  const list = lastScan && lastScan.recommendations ? lastScan.recommendations : [];
  const home = $("homeRecommendations");
  home.classList.toggle("empty", list.length === 0);
  home.innerHTML = "";
  if (!list.length) {
    home.textContent = "Nenhuma recomendação pendente — faça uma análise.";
    return;
  }
  list.forEach(function (item) { home.appendChild(recommendationNode(item)); });
}

function findingNode(finding) {
  const div = document.createElement("div");
  div.className = "row";
  const status = String(finding.status || "");
  const key = status.toLowerCase();
  const blockers = (finding.blockers || []).map(function (code) {
    return BLOCKER_LABELS[code] || code.toLowerCase().replace(/_/g, " ");
  });
  const reasons = [];
  if (finding.reason) reasons.push(finding.reason);
  if (blockers.length) reasons.push("Impedido porque " + blockers.join("; ") + ".");
  const meta = [];
  if (finding.benefit) meta.push("Benefício: " + String(finding.benefit).toLowerCase().replace(/_/g, " "));
  if (finding.risk) meta.push("Risco: " + String(finding.risk).toLowerCase());
  if (finding.confidence) meta.push("Confiança: " + String(finding.confidence).toLowerCase());
  if (finding.automation_eligible) meta.push("Pode ser aplicado com consentimento");

  const actionable = finding.action_id === "SET_ACTIVE_POWER_PLAN";
  div.innerHTML =
    "<div class='row-main'><h4>" + escapeHtml(finding.title) + "</h4>" +
    "<p>" + escapeHtml(reasons.join(" ")) + "</p>" +
    "<small>" + escapeHtml(meta.join(" · ")) + "</small>" +
    (actionable
      ? "<button class='ghost-btn row-action'>Aplicar com consentimento…</button>"
      : "") +
    "</div>" +
    "<span class='tag tag-" + escapeHtml(key) + "'>" +
    escapeHtml(FINDING_LABELS[status] || status) + "</span>";
  if (actionable) {
    div.querySelector(".row-action").addEventListener("click", function () {
      applyFindingAction();
    });
  }
  return div;
}

function renderPlan() {
  const plan = lastPlan || (lastScan && lastScan.optimization_plan) || null;
  const list = $("planList");
  if (!plan || !plan.findings || !plan.findings.length) {
    list.classList.add("empty");
    list.textContent = "Faça uma análise para gerar o plano.";
    $("applyCount").textContent = "0";
    $("reviewCount").textContent = "0";
    $("blockedCount").textContent = "0";
    $("okCount").textContent = "0";
    return;
  }
  lastPlan = plan;
  const counts = plan.summary && plan.summary.status_counts ? plan.summary.status_counts : {};
  $("applyCount").textContent = counts.APPLY_RECOMMENDED || 0;
  $("reviewCount").textContent = counts.MANUAL_REVIEW || 0;
  $("blockedCount").textContent = counts.BLOCKED || 0;
  $("okCount").textContent = counts.ALREADY_CORRECT || 0;
  list.classList.remove("empty");
  list.innerHTML = "";
  plan.findings.forEach(function (finding) { list.appendChild(findingNode(finding)); });
}

/* ---------- resumo e hardware ---------- */

function chip(label, value) {
  return "<div class='chip'><span>" + escapeHtml(label) + "</span><strong>" +
    escapeHtml(value) + "</strong></div>";
}

function renderSummary() {
  const box = $("machineSummary");
  if (!lastScan) {
    box.classList.add("empty");
    box.textContent = "Faça uma análise para ver o resumo da máquina.";
    return;
  }
  const cpu = metric("CPU", "model") || "CPU não identificada";
  const gpuAdapters = metric("GPU", "adapters") || [];
  const gpu = metric("GPU", "name") || (gpuAdapters[0] && gpuAdapters[0].Name) || "GPU não identificada";
  const ram = metric("RAM", "installed_gb");
  const available = metric("RAM", "available_gb");
  const windows = metric("Windows", "edition") || "Windows";
  const boot = metric("Windows", "last_boot");
  const volumes = metric("Storage", "volumes") || [];
  const systemVolume = volumes.find(function (v) {
    return String(v.root || "").toUpperCase().startsWith("C:");
  }) || volumes[0];

  const chips = [];
  chips.push(chip("Sistema", windows));
  chips.push(chip("Processador", cpu));
  chips.push(chip("Memória", (ram != null ? ram + " GB" : "—") +
    (available != null ? " · " + available + " GB livres" : "")));
  chips.push(chip("Placa de vídeo", gpu));
  if (systemVolume) {
    chips.push(chip("Disco C:",
      (systemVolume.free_gb != null ? systemVolume.free_gb.toFixed(0) + " GB livres de " : "") +
      (systemVolume.total_gb != null ? systemVolume.total_gb.toFixed(0) + " GB" : "")));
  }
  chips.push(chip("Ligado desde", formatDate(boot)));

  box.classList.remove("empty");
  box.innerHTML = chips.join("");
}

function renderHardware() {
  const hero = $("hardwareHero");
  const container = $("domainGroups");
  if (!lastScan) {
    hero.classList.add("empty");
    hero.textContent = "Faça uma análise para identificar o hardware.";
    container.innerHTML = "";
    return;
  }

  const cpu = metric("CPU", "model") || "CPU não identificada";
  const gpuAdapters = metric("GPU", "adapters") || [];
  const gpu = metric("GPU", "name") || (gpuAdapters[0] && gpuAdapters[0].Name) || "GPU não identificada";
  const ram = metric("RAM", "installed_gb");
  const windows = metric("Windows", "edition") || "Windows";

  hero.classList.remove("empty");
  hero.innerHTML =
    chip("Processador", cpu) +
    chip("Placa de vídeo", gpu) +
    chip("Memória", ram != null ? ram + " GB" : "—") +
    chip("Sistema", windows);

  const shown = new Set();
  const groups = DOMAIN_GROUPS.map(function (group) {
    const domains = group.domains.filter(function (name) {
      return lastScan.snapshot.some(function (item) { return item.domain === name; });
    });
    domains.forEach(function (name) { shown.add(name); });
    return { title: group.title, domains: domains };
  }).filter(function (group) { return group.domains.length; });

  const orphans = lastScan.snapshot.filter(function (item) { return !shown.has(item.domain); });
  if (orphans.length) {
    groups.push({ title: "Outros", domains: orphans.map(function (item) { return item.domain; }) });
  }

  container.innerHTML = "";
  groups.forEach(function (group) {
    const section = document.createElement("section");
    section.className = "domain-group";
    section.innerHTML = "<h3>" + escapeHtml(group.title) + "</h3>";
    const grid = document.createElement("div");
    grid.className = "domain-grid";

    group.domains.forEach(function (domainName) {
      const item = lastScan.snapshot.find(function (entry) { return entry.domain === domainName; });
      if (!item) return;
      const card = document.createElement("article");
      card.className = "domain-card";
      const statusKey = String(item.status || "").toLowerCase();
      const rows = (item.metrics || []).map(function (m) {
        const label = METRIC_LABELS[m.name] || m.name.replace(/_/g, " ");
        const value = formatValue(m.name, m.value, m.unit);
        if (value === "—" && (m.value == null)) return "";
        return "<div class='kv'><span>" + escapeHtml(label) + "</span><b>" +
          escapeHtml(value) + "</b></div>";
      }).join("");

      card.innerHTML =
        "<header><h4>" + escapeHtml(item.domain) + "</h4>" +
        "<span class='tag tag-" + escapeHtml(statusKey) + "'>" +
        escapeHtml(STATUS_LABELS[item.status] || item.status) + "</span></header>" +
        (item.message ? "<p class='msg'>" + escapeHtml(item.message) + "</p>" : "") +
        (rows || "<p class='msg'>Sem dados expostos por este domínio.</p>");
      grid.appendChild(card);
    });

    section.appendChild(grid);
    container.appendChild(section);
  });
}

/* ---------- ações ---------- */

async function scan(button) {
  const buttons = [$("scanButton"), $("scanButton2")];
  buttons.forEach(function (b) { b.disabled = true; b.textContent = "Analisando…"; });
  $("overallStatus").textContent = "Analisando o sistema";
  $("overallText").textContent = "Lendo hardware, Windows, energia, segurança, inicialização e armazenamento…";

  try {
    const response = await fetch("/api/scan", { cache: "no-store" });
    if (!response.ok) throw new Error("Falha ao analisar o PC.");
    lastScan = await response.json();
    lastPlan = lastScan.optimization_plan || lastPlan;
    applyScan();
    toast("Análise concluída.");
  } catch (error) {
    $("overallStatus").textContent = "Falha na análise";
    $("overallText").textContent = error.message;
    toast(error.message);
  } finally {
    buttons.forEach(function (b) { b.disabled = false; });
    $("scanButton").textContent = "Analisar novamente";
    $("scanButton2").textContent = "Analisar de novo";
  }
}

function applyScan() {
  const list = lastScan.recommendations || [];
  const issues = list.filter(function (x) { return x.status === "RECOMMENDED"; }).length;
  const manual = list.filter(function (x) { return x.status === "MANUAL_REVIEW"; }).length;

  $("overallStatus").textContent = issues
    ? issues + " recomendação(ões) pronta(s)"
    : (manual ? manual + " item(ns) para revisão" : "Nenhuma ação crítica");
  $("overallTitle").textContent = issues
    ? "Seu PC tem oportunidades concretas de melhoria."
    : "Seu PC não precisa de mudanças agressivas agora.";
  $("overallText").textContent = issues
    ? "Abra “Otimizar” para ver cada item com motivo, benefício e risco. Nada é aplicado sem consentimento."
    : "O Zeus analisou hardware, Windows, energia, segurança, inicialização e armazenamento.";

  const when = lastScan.created_at ? formatDate(lastScan.created_at) : "agora";
  $("scanMeta").textContent = "Última análise: " + when;

  renderSummary();
  renderRecommendations();
  renderHardware();
  renderPlan();
  if ($("technicalDrawer").classList.contains("open")) {
    $("technicalJson").textContent = JSON.stringify(lastScan, null, 2);
  }
}

/* ---------- observer ao vivo ---------- */

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
  ctx.strokeStyle = "#66e08a";
  ctx.lineWidth = 2.5;
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
    if (!response.ok) return;
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

async function refreshProcesses() {
  try {
    const response = await fetch("/api/observer/processes?limit=1", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    const latest = data.latest;
    const box = $("processList");
    if (!latest || !latest.top_memory || !latest.top_memory.length) {
      box.classList.add("empty");
      box.textContent = "Aguardando o observer coletar processos.";
      return;
    }
    box.classList.remove("empty");
    box.innerHTML = "";
    latest.top_memory.slice(0, 6).forEach(function (proc) {
      const row = document.createElement("div");
      row.className = "row";
      const memory = proc.working_set_bytes != null ? formatBytes(proc.working_set_bytes) : "—";
      row.innerHTML =
        "<div class='row-main'><h4>" + escapeHtml(proc.name || proc.Name || "—") + "</h4>" +
        "<small>PID " + escapeHtml(proc.pid != null ? proc.pid : "—") +
        (proc.cpu_pct != null ? " · CPU " + escapeHtml(proc.cpu_pct) + "%" : "") + "</small></div>" +
        "<span class='tag'>" + escapeHtml(memory) + "</span>";
      box.appendChild(row);
    });
  } catch (error) {
    console.debug(error);
  }
}

/* ---------- cérebro (IA local) ---------- */

async function renderBrain() {
  const box = $("brainCard");
  try {
    const response = await fetch("/api/brain/status", { cache: "no-store" });
    if (!response.ok) throw new Error("status indisponível");
    const report = await response.json();
    const checks = report.checks || [];
    const rows = checks.map(function (check) {
      const ok = check.ok ? "ok" : "pendente";
      return "<div class='kv'><span>" + escapeHtml(check.name) + "</span><b class='" +
        (check.ok ? "good" : "warn") + "'>" + escapeHtml(ok) + "</b></div>" +
        "<div class='kv kv-sub'><span>detalhe</span><b>" +
        escapeHtml(check.detail || "—") + "</b></div>";
    }).join("");
    const installed = report.installed_models != null
      ? report.installed_models + " de " + report.registered_models + " modelos instalados"
      : "";
    box.innerHTML =
      "<div class='kv'><span>Estado geral</span><b class='" +
      (report.ok ? "good" : "warn") + "'>" + (report.ok ? "saudável" : "incompleto") + "</b></div>" +
      (installed ? "<div class='kv'><span>Modelos</span><b>" + escapeHtml(installed) + "</b></div>" : "") +
      rows;
  } catch (error) {
    box.innerHTML = "<div class='kv'><span>IA local</span><b>não respondeu — inicie o servidor</b></div>";
  }
}

/* ---------- painel Sistema ---------- */

function panelRows(title, domainName) {
  const item = (lastScan && lastScan.snapshot || []).find(function (entry) {
    return entry.domain === domainName;
  });
  if (!item) return "<p class='msg'>Faça uma análise primeiro.</p>";
  const rows = (item.metrics || []).map(function (m) {
    const label = METRIC_LABELS[m.name] || m.name.replace(/_/g, " ");
    return "<div class='kv'><span>" + escapeHtml(label) + "</span><b>" +
      escapeHtml(formatValue(m.name, m.value, m.unit)) + "</b></div>";
  }).join("");
  return "<h4 class='panel-title'>" + escapeHtml(title) + "</h4>" + (rows || "<p class='msg'>Sem dados.</p>");
}

document.querySelectorAll(".tile").forEach(function (tile) {
  tile.addEventListener("click", async function () {
    const type = tile.dataset.more;
    const panel = $("morePanel");
    document.querySelectorAll(".tile").forEach(function (t) { t.classList.toggle("active", t === tile); });

    if (type === "power") {
      await renderPowerPanel();
      return;
    }

    const map = {
      updates: ["Atualizações do Windows", "Windows Update"],
      startup: ["Programas na inicialização", "Startup"],
      security: ["Segurança do sistema", "Security"],
    };
    const entry = map[type];
    panel.innerHTML = panelRows(entry[0], entry[1]);
  });
});

/* ---------- transações reais: preparar → consentir → executar ---------- */

const ACTION_LABELS = { SET_ACTIVE_POWER_PLAN: "Trocar o plano de energia" };

async function postJson(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await response.json(); } catch (error) { data = {}; }
  if (!response.ok) throw new Error(data.message || "Erro " + response.status);
  return data;
}

function txStatus(html, kind) {
  const box = $("txStatus") || $("planStatus");
  if (!box) return;
  box.className = "tx-status" + (kind ? " " + kind : "");
  box.innerHTML = html;
}

function buildModal(innerHtml) {
  const root = document.createElement("div");
  root.className = "modal-overlay";
  root.innerHTML = "<div class='modal-card'>" + innerHtml + "</div>";
  document.body.appendChild(root);
  const close = function () { root.remove(); };
  root.addEventListener("click", function (event) {
    if (event.target === root) close();
  });
  return { root: root, close: close };
}

function oneLine(value) {
  if (value == null) return "—";
  if (typeof value !== "object") return String(value);
  const parts = Object.keys(value).map(function (key) {
    const item = value[key];
    return item == null || typeof item === "object" ? null : String(item);
  }).filter(Boolean);
  return parts.join(" · ") || "—";
}

function consentDialog(tx, label) {
  return new Promise(function (resolve) {
    const expires = String(tx.consent_expires_at || "")
      .replace("T", " ").split(".")[0].replace(/Z$/, "") + " (UTC)";
    const modal = buildModal(
      "<h4>Confirmar alteração</h4>" +
      "<p class='modal-sub'>" + escapeHtml(label) + "</p>" +
      "<div class='kv'><span>o que vai mudar</span><b>" +
      escapeHtml(ACTION_LABELS[tx.action_id] || tx.action_id) + "</b></div>" +
      "<div class='kv'><span>estado atual</span><b>" +
      escapeHtml(oneLine(tx.before_state)) + "</b></div>" +
      "<div class='kv'><span>como desfazer</span><b>" +
      escapeHtml(oneLine(tx.rollback_preview)) + "</b></div>" +
      "<div class='kv'><span>consentimento</span><b>válido até " +
      escapeHtml(expires) + "</b></div>" +
      "<div class='modal-actions'>" +
      "<button class='primary-btn' id='txConfirm'>Confirmar e aplicar</button>" +
      "<button class='ghost-btn' id='txCancel'>Cancelar</button></div>");
    modal.root.querySelector("#txConfirm").addEventListener("click", function () {
      modal.close();
      resolve(true);
    });
    modal.root.querySelector("#txCancel").addEventListener("click", function () {
      modal.close();
      resolve(false);
    });
  });
}

function choosePowerPlan() {
  return fetch("/api/power-plans", { cache: "no-store" })
    .then(function (response) { return response.json(); })
    .then(function (data) {
      return new Promise(function (resolve) {
        const inactive = (data.plans || []).filter(function (plan) {
          return !plan.active;
        });
        if (!inactive.length) {
          const modal = buildModal(
            "<h4>Planos de energia</h4>" +
            "<p class='modal-sub'>Nenhum plano alternativo — o ativo já está aplicado.</p>" +
            "<div class='modal-actions'><button class='ghost-btn' id='txCancel'>Fechar</button></div>");
          modal.root.querySelector("#txCancel").addEventListener("click", function () {
            modal.close();
            resolve(null);
          });
          return;
        }
        const items = inactive.map(function (plan, index) {
          return "<label class='plan-pick'><input type='radio' name='planPick' value='" +
            escapeHtml(plan.guid) + "' data-name='" + escapeHtml(plan.name) + "'" +
            (index === 0 ? " checked" : "") + "><span>" + escapeHtml(plan.name) +
            "</span></label>";
        }).join("");
        const modal = buildModal(
          "<h4>Escolher plano de energia</h4>" +
          "<p class='modal-sub'>A troca é verificada pelo Windows e pode ser desfeita.</p>" +
          "<div class='plan-list'>" + items + "</div>" +
          "<div class='modal-actions'>" +
          "<button class='primary-btn' id='txConfirm'>Continuar</button>" +
          "<button class='ghost-btn' id='txCancel'>Cancelar</button></div>");
        modal.root.querySelector("#txCancel").addEventListener("click", function () {
          modal.close();
          resolve(null);
        });
        modal.root.querySelector("#txConfirm").addEventListener("click", function () {
          const selected = modal.root.querySelector("input[name='planPick']:checked");
          if (!selected) return;
          const chosen = { guid: selected.value, name: selected.dataset.name };
          modal.close();
          resolve(chosen);
        });
      });
    });
}

async function renderPowerPanel(statusHtml, statusKind) {
  const panel = $("morePanel");
  try {
    const response = await fetch("/api/power-plans", { cache: "no-store" });
    const data = await response.json();
    const rows = (data.plans || []).map(function (plan) {
      const action = plan.active
        ? "<b class='good'>ativo</b>"
        : "<button class='ghost-btn tx-btn' data-guid='" + escapeHtml(plan.guid) +
          "' data-name='" + escapeHtml(plan.name) + "'>Ativar</button>";
      return "<div class='kv'><span>" + escapeHtml(plan.name) + "</span>" +
        action + "</div>";
    }).join("");
    panel.innerHTML = "<h4 class='panel-title'>Planos de energia</h4>" +
      "<div id='txStatus' class='tx-status'></div>" +
      (rows || "<p class='msg'>Nenhum plano encontrado.</p>");
    if (statusHtml) txStatus(statusHtml, statusKind);
    panel.querySelectorAll(".tx-btn").forEach(function (button) {
      button.addEventListener("click", async function () {
        button.disabled = true;
        const result = await applyPowerPlan(button.dataset.guid, button.dataset.name);
        button.disabled = false;
        if (result && result.ok) {
          await renderPowerPanel(
            "✓ " + escapeHtml(result.message || "Plano alterado e verificado."),
            "ok"
          );
        }
      });
    });
  } catch (error) {
    panel.innerHTML = "<p class='msg'>Não foi possível ler os planos de energia.</p>";
  }
}

async function applyPowerPlan(guid, planName) {
  txStatus("Preparando a alteração…", "wait");
  try {
    const prepared = await postJson("/api/transactions/prepare", {
      action_id: "SET_ACTIVE_POWER_PLAN",
      args: { guid: guid },
    });
    const tx = prepared.transaction;
    const confirmed = await consentDialog(tx, "Plano de energia → " + planName);
    if (!confirmed) {
      txStatus("Cancelado — nada foi alterado.", "info");
      return null;
    }
    await postJson("/api/transactions/consent", {
      transaction_id: tx.transaction_id,
      consent_token: tx.consent_token,
    });
    const result = await postJson("/api/transactions/execute", {
      transaction_id: tx.transaction_id,
    });
    if (!result.ok) {
      txStatus(
        "✗ " + escapeHtml(result.message || "A alteração não foi confirmada."),
        "err"
      );
    }
    return result;
  } catch (error) {
    txStatus("✗ " + escapeHtml(error.message || String(error)), "err");
    return null;
  }
}

async function applyFindingAction() {
  const choice = await choosePowerPlan();
  if (!choice) {
    txStatus("Cancelado — nada foi alterado.", "info");
    return;
  }
  const result = await applyPowerPlan(choice.guid, choice.name);
  if (result && result.ok) {
    txStatus(
      "✓ " + escapeHtml(result.message || "Plano alterado e verificado."),
      "ok"
    );
  }
  refreshOptimizationPlan();
}

async function refreshOptimizationPlan() {
  try {
    const response = await fetch("/api/optimization/plan", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    lastPlan = data.plan;
    renderPlan();
  } catch (error) {
    console.debug(error);
  }
}

/* ---------- eventos ---------- */

$("scanButton").addEventListener("click", function () { scan($("scanButton")); });
$("scanButton2").addEventListener("click", function () { scan($("scanButton2")); });

$("technicalToggle").addEventListener("click", function () {
  $("technicalDrawer").classList.add("open");
  $("technicalJson").textContent = lastScan ? JSON.stringify(lastScan, null, 2)
    : "Nenhum diagnóstico carregado.";
});
$("closeTechnical").addEventListener("click", function () {
  $("technicalDrawer").classList.remove("open");
});

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

/* ---------- init ---------- */

async function loadLastScan() {
  try {
    const response = await fetch("/api/history?limit=1", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    const latest = data.scans && data.scans[0];
    if (!latest) return;
    lastScan = {
      snapshot: latest.snapshot,
      recommendations: latest.recommendations,
      created_at: latest.created_at,
      profile: latest.profile,
    };
    applyScan();
    const planResponse = await fetch("/api/optimization/plan", { cache: "no-store" });
    if (planResponse.ok) {
      const planData = await planResponse.json();
      lastPlan = planData.plan;
      renderPlan();
    }
  } catch (error) {
    console.debug(error);
  }
}

(async function init() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    const status = await response.json();
    $("buildText").textContent = "v" + status.version + " · " + status.build;
  } catch (error) {
    $("buildText").textContent = "Offline";
  }
  sample();
  observerTimer = setInterval(sample, 3000);
  loadLastScan();
  renderBrain();
  refreshProcesses();
})();
