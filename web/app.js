const $ = (id) => document.getElementById(id);

let lastScan = null;
let observerTimer = null;
const series = { cpu: [], gpu: [], ram: [] };

let powerPlans = [];
let powerTxCache = [];
let powerPending = null;
let powerBusy = false;
let powerCountdownTimer = null;

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

function powerApi(url, payload) {
  const options = payload === undefined
    ? { cache: "no-store" }
    : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      };
  return fetch(url, options).then(function (response) {
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

function formatStamp(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleString("pt-BR");
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

    stopConsentCountdown();
    powerPending = null;
    panel.className = "detail-panel";

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
