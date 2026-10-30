const operationDialog = document.querySelector("#operation-dialog");
const operationError = document.querySelector("#operation-error");
const operationState = { jobs: [], candidates: [], users: [], applications: [], offers: [], interviews: [], rounds: [] };

const applicationTransitions = {
  applied: ["screening", "rejected", "withdrawn"],
  screening: ["interview", "rejected", "withdrawn"],
  interview: ["offer", "rejected", "withdrawn"],
  offer: ["hired", "rejected", "withdrawn"],
  hired: [], rejected: [], withdrawn: [],
};
const offerTransitions = { draft: ["sent", "withdrawn"], sent: ["accepted", "declined", "withdrawn"], accepted: [], declined: [], withdrawn: [] };
const stageNames = { applied: "已申请", screening: "筛选中", interview: "面试中", offer: "Offer", hired: "已录用", rejected: "已拒绝", withdrawn: "已撤回" };
const offerNames = { draft: "草稿", sent: "已发送", accepted: "已接受", declined: "已拒绝", withdrawn: "已撤回" };

function operationToken() { return localStorage.getItem("hireflow.access_token"); }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]); }
function setOperationError(message) { operationError.textContent = message; operationError.style.display = "block"; }
function clearOperationError() { operationError.style.display = "none"; }
function operationToast(message) { const box = document.querySelector("#toast"); box.textContent = message; box.classList.add("show"); window.setTimeout(() => box.classList.remove("show"), 2600); }

async function operationApi(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  headers.Authorization = `Bearer ${operationToken()}`;
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) { const error = new Error(data.detail || `请求失败（${response.status}）`); error.status = response.status; throw error; }
  return data;
}

function byId(items, id) { return items.find((item) => item.id === id); }
function jobName(id) { return byId(operationState.jobs, id)?.title || id; }
function candidateName(id) { return byId(operationState.candidates, id)?.full_name || id; }
function selectOptions(items, label) { return items.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(label(item))}</option>`).join(""); }

async function refreshOperationState() {
  const resources = [
    ["jobs", "/v1/jobs"], ["candidates", "/v1/candidates"], ["users", "/v1/users"],
    ["applications", "/v1/applications"], ["offers", "/v1/offers"], ["interviews", "/v1/interviews"],
  ];
  const results = await Promise.allSettled(resources.map(([, path]) => operationApi(path)));
  results.forEach((result, index) => { if (result.status === "fulfilled") operationState[resources[index][0]] = result.value; });
}

async function loadRounds(jobId) {
  operationState.rounds = jobId ? await operationApi(`/v1/jobs/${jobId}/rounds`) : [];
  const select = document.querySelector("#schedule-round");
  select.innerHTML = '<option value="">不指定轮次</option>' + selectOptions(operationState.rounds, (round) => `${round.sequence}. ${round.name} · ${round.duration_minutes} 分钟`);
}

function populateCoreSelects() {
  const jobOptions = selectOptions(operationState.jobs, (job) => `${job.title} · ${job.status}`);
  const candidateOptions = selectOptions(operationState.candidates, (candidate) => `${candidate.full_name} · ${candidate.email}`);
  document.querySelector("#application-job").innerHTML = jobOptions;
  document.querySelector("#application-candidate").innerHTML = candidateOptions;
  document.querySelector("#round-job").innerHTML = jobOptions;
  document.querySelector("#schedule-job").innerHTML = jobOptions;
  document.querySelector("#schedule-candidate").innerHTML = candidateOptions;
  document.querySelector("#schedule-interviewer").innerHTML = selectOptions(operationState.users, (user) => `${user.display_name} · ${user.role}`);
  document.querySelector("#schedule-application").innerHTML = '<option value="">不关联 ATS 申请</option>' + selectOptions(operationState.applications, (item) => `${candidateName(item.candidate_id)} · ${jobName(item.job_id)} · ${stageNames[item.stage]}`);
  document.querySelector("#stage-application").innerHTML = selectOptions(operationState.applications, (item) => `${candidateName(item.candidate_id)} · ${jobName(item.job_id)} · ${stageNames[item.stage]}`);
  document.querySelector("#offer-application").innerHTML = selectOptions(operationState.applications.filter((item) => item.stage === "offer"), (item) => `${candidateName(item.candidate_id)} · ${jobName(item.job_id)}`);
  document.querySelector("#offer-status-offer").innerHTML = selectOptions(operationState.offers, (item) => `${candidateName(byId(operationState.applications, item.application_id)?.candidate_id)} · ${offerNames[item.status]}`);
  document.querySelector("#scorecard-interview").innerHTML = selectOptions(operationState.interviews, (item) => `${item.candidate_name} · ${item.job_title} · ${new Date(item.starts_at).toLocaleString("zh-CN")}`);
}

function fillStageTargets() {
  const application = byId(operationState.applications, document.querySelector("#stage-application").value);
  const targets = application ? applicationTransitions[application.stage] : [];
  document.querySelector("#stage-target").innerHTML = targets.map((stage) => `<option value="${stage}">${stageNames[stage]}</option>`).join("");
}

function fillOfferTargets() {
  const offer = byId(operationState.offers, document.querySelector("#offer-status-offer").value);
  const targets = offer ? offerTransitions[offer.status] : [];
  document.querySelector("#offer-status-target").innerHTML = targets.map((status) => `<option value="${status}">${offerNames[status]}</option>`).join("");
}

async function openOperation(kind, selectedId = null) {
  if (!operationToken()) { document.querySelector("#open-auth").click(); operationToast("请先连接招聘团队"); return; }
  clearOperationError();
  document.querySelectorAll(".operation-view").forEach((view) => view.classList.add("hidden"));
  const view = document.querySelector(`#${kind}-view`);
  if (!view) return;
  view.classList.remove("hidden");
  const titles = {
    job: ["NEW JOB", "新建招聘职位"], candidate: ["NEW CANDIDATE", "录入候选人"], schedule: ["SCHEDULE INTERVIEW", "安排面试"],
    application: ["NEW APPLICATION", "启动招聘申请"], stage: ["PIPELINE TRANSITION", "推进申请阶段"], round: ["INTERVIEW PLAN", "配置面试轮次"],
    scorecard: ["STRUCTURED FEEDBACK", "填写面试评分卡"], offer: ["DRAFT OFFER", "起草 Offer"], "offer-status": ["OFFER TRANSITION", "更新 Offer 状态"],
  };
  document.querySelector("#operation-kicker").textContent = titles[kind][0];
  document.querySelector("#operation-title").textContent = titles[kind][1];
  try {
    await refreshOperationState();
    populateCoreSelects();
    if (kind === "job") document.querySelector("#job-manager").innerHTML = '<option value="">暂不指定</option>' + selectOptions(operationState.users, (user) => `${user.display_name} · ${user.role}`);
    if (kind === "round" && !operationState.jobs.length) throw new Error("请先至少创建一个职位。");
    if (["application", "schedule"].includes(kind) && (!operationState.jobs.length || !operationState.candidates.length)) throw new Error("请先至少创建一位候选人和一个职位。");
    if (kind === "schedule") {
      if (!operationState.users.length) throw new Error("请先创建面试官用户。");
      await loadRounds(document.querySelector("#schedule-job").value);
    }
    if (kind === "stage") {
      if (!operationState.applications.length) throw new Error("暂无可推进的申请。");
      if (selectedId) document.querySelector("#stage-application").value = selectedId;
      fillStageTargets();
      if (!document.querySelector("#stage-target").options.length) throw new Error("该申请已处于终态，不能继续推进。");
    }
    if (kind === "offer" && !operationState.applications.some((item) => item.stage === "offer")) throw new Error("请先将申请推进到 Offer 阶段。");
    if (kind === "offer-status") {
      if (!operationState.offers.length) throw new Error("暂无可更新的 Offer。");
      if (selectedId) document.querySelector("#offer-status-offer").value = selectedId;
      fillOfferTargets();
      if (!document.querySelector("#offer-status-target").options.length) throw new Error("该 Offer 已处于终态。");
    }
    if (kind === "scorecard" && !operationState.interviews.length) throw new Error("暂无可填写评分卡的面试。");
    operationDialog.showModal();
  } catch (error) { operationToast(error.message); }
}

async function completeOperation(successMessage, action) {
  clearOperationError();
  try {
    await action();
    operationDialog.close();
    await Promise.all([loadOverview(), loadRecords()]);
    operationToast(successMessage);
  } catch (error) {
    const conflict = error.status === 409 ? `并发或状态冲突：${error.message}（409 Conflict）` : error.message;
    setOperationError(conflict);
  }
}

function renderSimpleRecords(target, items, primary, secondary) {
  const container = document.querySelector(target);
  if (!items.length) { container.className = "record-list empty-state"; container.innerHTML = "<p>暂无数据，使用上方快捷操作开始创建。</p>"; return; }
  container.className = "record-list";
  container.innerHTML = items.slice(0, 5).map((item) => `<div class="record-row"><span><strong>${escapeHtml(primary(item))}</strong><small>${escapeHtml(secondary(item))}</small></span><b>›</b></div>`).join("");
}

function renderPipeline() {
  const container = document.querySelector("#application-list");
  if (!operationState.applications.length) { container.className = "record-list empty-state"; container.innerHTML = "<p>暂无申请，关联职位与候选人后启动流程。</p>"; }
  else {
    container.className = "record-list";
    container.innerHTML = operationState.applications.slice(0, 6).map((item) => `<div class="record-row"><span><strong>${escapeHtml(candidateName(item.candidate_id))}<em class="stage-pill">${escapeHtml(stageNames[item.stage])}</em></strong><small>${escapeHtml(jobName(item.job_id))} · v${item.version}</small></span><span class="record-actions">${applicationTransitions[item.stage].length ? `<button class="record-action" data-advance="${item.id}">推进</button>` : ""}</span></div>`).join("");
  }
  const offers = document.querySelector("#offer-list");
  if (!operationState.offers.length) { offers.className = "record-list empty-state"; offers.innerHTML = "<p>暂无 Offer，申请进入 Offer 阶段后可起草。</p>"; return; }
  offers.className = "record-list";
  offers.innerHTML = operationState.offers.slice(0, 6).map((item) => { const app = byId(operationState.applications, item.application_id); return `<div class="record-row"><span><strong>${escapeHtml(candidateName(app?.candidate_id))}<em class="stage-pill">${escapeHtml(offerNames[item.status])}</em></strong><small>${escapeHtml((item.salary_amount / 100).toLocaleString("zh-CN"))} ${escapeHtml(item.currency)} · v${item.version}</small></span><span class="record-actions">${offerTransitions[item.status].length ? `<button class="record-action" data-offer-action="${item.id}">更新</button>` : ""}</span></div>`; }).join("");
}

async function loadRecords() {
  if (!operationToken()) return;
  try {
    await refreshOperationState();
    renderSimpleRecords("#job-list", operationState.jobs, (job) => job.title, (job) => `状态：${job.status}`);
    renderSimpleRecords("#candidate-list", operationState.candidates, (candidate) => candidate.full_name, (candidate) => candidate.email);
    renderPipeline();
  } catch (_) { /* dashboard handles expired sessions */ }
}

document.querySelectorAll(".quick-action,.mini-action").forEach((button) => button.addEventListener("click", () => openOperation(button.dataset.operation)));
document.addEventListener("click", (event) => {
  const advance = event.target.closest("[data-advance]");
  const offerAction = event.target.closest("[data-offer-action]");
  if (advance) openOperation("stage", advance.dataset.advance);
  if (offerAction) openOperation("offer-status", offerAction.dataset.offerAction);
});

document.querySelector("#job-submit").addEventListener("click", () => completeOperation("职位已创建", () => operationApi("/v1/jobs", { method: "POST", body: JSON.stringify({ title: document.querySelector("#job-title").value.trim(), hiring_manager_id: document.querySelector("#job-manager").value || null }) })));
document.querySelector("#candidate-submit").addEventListener("click", () => completeOperation("候选人已录入", () => operationApi("/v1/candidates", { method: "POST", body: JSON.stringify({ full_name: document.querySelector("#candidate-name").value.trim(), email: document.querySelector("#candidate-email").value.trim(), phone: document.querySelector("#candidate-phone").value.trim() || null }) })));
document.querySelector("#application-submit").addEventListener("click", () => completeOperation("申请已创建，ATS 流程已启动", () => operationApi("/v1/applications", { method: "POST", body: JSON.stringify({ job_id: document.querySelector("#application-job").value, candidate_id: document.querySelector("#application-candidate").value }) })));
document.querySelector("#stage-application").addEventListener("change", fillStageTargets);
document.querySelector("#stage-submit").addEventListener("click", () => completeOperation("申请阶段已更新并写入审计", () => { const item = byId(operationState.applications, document.querySelector("#stage-application").value); return operationApi(`/v1/applications/${item.id}/stage`, { method: "POST", body: JSON.stringify({ stage: document.querySelector("#stage-target").value, version: item.version, reason: document.querySelector("#stage-reason").value.trim() || null }) }); }));
document.querySelector("#round-submit").addEventListener("click", () => completeOperation("面试轮次已创建", () => { const criteria = document.querySelector("#round-criteria").value.split(",").map((value) => value.trim()).filter(Boolean).map((name) => ({ name })); return operationApi(`/v1/jobs/${document.querySelector("#round-job").value}/rounds`, { method: "POST", body: JSON.stringify({ name: document.querySelector("#round-name").value.trim(), sequence: Number(document.querySelector("#round-sequence").value), duration_minutes: Number(document.querySelector("#round-duration").value), criteria }) }); }));

document.querySelector("#schedule-job").addEventListener("change", (event) => loadRounds(event.target.value).catch((error) => setOperationError(error.message)));
document.querySelector("#schedule-application").addEventListener("change", async (event) => { const item = byId(operationState.applications, event.target.value); if (!item) return; document.querySelector("#schedule-candidate").value = item.candidate_id; document.querySelector("#schedule-job").value = item.job_id; await loadRounds(item.job_id); });
document.querySelector("#suggest-submit").addEventListener("click", async () => {
  clearOperationError();
  try {
    const startsAt = new Date(document.querySelector("#availability-start").value); const endsAt = new Date(document.querySelector("#availability-end").value);
    if (Number.isNaN(startsAt.valueOf()) || Number.isNaN(endsAt.valueOf())) throw new Error("请先填写双方可用的开始与结束时间。");
    const candidateId = document.querySelector("#schedule-candidate").value; const interviewerId = document.querySelector("#schedule-interviewer").value;
    await operationApi(`/v1/candidates/${candidateId}/availability`, { method: "POST", body: JSON.stringify({ starts_at: startsAt.toISOString(), ends_at: endsAt.toISOString() }) });
    await operationApi(`/v1/interviewers/${interviewerId}/availability`, { method: "POST", body: JSON.stringify({ starts_at: startsAt.toISOString(), ends_at: endsAt.toISOString() }) });
    const round = byId(operationState.rounds, document.querySelector("#schedule-round").value);
    const suggestion = await operationApi("/v1/scheduling/suggestions", { method: "POST", body: JSON.stringify({ candidate_id: candidateId, interviewer_id: interviewerId, duration_minutes: round?.duration_minutes || 30 }) });
    document.querySelector("#schedule-start").value = new Date(suggestion.starts_at).toISOString().slice(0, 16); document.querySelector("#schedule-end").value = new Date(suggestion.ends_at).toISOString().slice(0, 16);
    operationToast("已计算最早共同可用时段；它尚未被预占。");
  } catch (error) { setOperationError(error.message); }
});
document.querySelector("#schedule-submit").addEventListener("click", () => completeOperation("面试已安排，通知正在异步投递", () => operationApi("/v1/interviews", { method: "POST", body: JSON.stringify({ candidate_id: document.querySelector("#schedule-candidate").value, interviewer_id: document.querySelector("#schedule-interviewer").value, job_id: document.querySelector("#schedule-job").value, application_id: document.querySelector("#schedule-application").value || null, round_id: document.querySelector("#schedule-round").value || null, starts_at: new Date(document.querySelector("#schedule-start").value).toISOString(), ends_at: new Date(document.querySelector("#schedule-end").value).toISOString(), meeting_url: document.querySelector("#schedule-url").value.trim() || null }) })));

document.querySelector("#scorecard-submit").addEventListener("click", () => completeOperation("评分卡已保存", () => operationApi(`/v1/interviews/${document.querySelector("#scorecard-interview").value}/scorecard`, { method: "PUT", body: JSON.stringify({ recommendation: document.querySelector("#scorecard-recommendation").value, overall_score: Number(document.querySelector("#scorecard-score").value), summary: document.querySelector("#scorecard-summary").value.trim() || null, answers: {}, submit: document.querySelector("#scorecard-submit-final").checked }) })));
document.querySelector("#offer-submit").addEventListener("click", () => completeOperation("Offer 草稿已创建", () => { const expires = document.querySelector("#offer-expires").value; return operationApi("/v1/offers", { method: "POST", body: JSON.stringify({ application_id: document.querySelector("#offer-application").value, salary_amount: Math.round(Number(document.querySelector("#offer-salary").value) * 100), currency: document.querySelector("#offer-currency").value.trim().toUpperCase(), start_date: document.querySelector("#offer-start-date").value || null, expires_at: expires ? new Date(expires).toISOString() : null }) }); }));
document.querySelector("#offer-status-offer").addEventListener("change", fillOfferTargets);
document.querySelector("#offer-status-submit").addEventListener("click", () => completeOperation("Offer 状态已更新", () => { const item = byId(operationState.offers, document.querySelector("#offer-status-offer").value); return operationApi(`/v1/offers/${item.id}/status`, { method: "POST", body: JSON.stringify({ status: document.querySelector("#offer-status-target").value, version: item.version }) }); }));

document.querySelector("#login-submit").addEventListener("click", () => window.setTimeout(loadRecords, 800));
document.querySelector("#bootstrap-submit").addEventListener("click", () => window.setTimeout(loadRecords, 800));
document.querySelector("#refresh").addEventListener("click", loadRecords);
loadRecords();
