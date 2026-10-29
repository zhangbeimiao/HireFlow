const operationDialog = document.querySelector("#operation-dialog");
const operationError = document.querySelector("#operation-error");

function operationToken() { return localStorage.getItem("hireflow.access_token"); }
function setOperationError(message) { operationError.textContent = message; operationError.style.display = "block"; }
function clearOperationError() { operationError.style.display = "none"; }
function operationToast(message) { const toast = document.querySelector("#toast"); toast.textContent = message; toast.classList.add("show"); window.setTimeout(() => toast.classList.remove("show"), 2600); }

async function operationApi(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  headers.Authorization = `Bearer ${operationToken()}`;
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) { const error = new Error(data.detail || `请求失败（${response.status}）`); error.status = response.status; throw error; }
  return data;
}

function selectOptions(items, label) { return items.map((item) => `<option value="${item.id}">${label(item)}</option>`).join(""); }

async function openOperation(kind) {
  if (!operationToken()) { document.querySelector("#open-auth").click(); operationToast("请先连接招聘团队"); return; }
  clearOperationError();
  document.querySelectorAll(".operation-view").forEach((view) => view.classList.add("hidden"));
  document.querySelector(`#${kind}-view`).classList.remove("hidden");
  const titles = { job: ["NEW JOB", "新建招聘职位"], candidate: ["NEW CANDIDATE", "录入候选人"], schedule: ["SCHEDULE INTERVIEW", "安排面试"] };
  document.querySelector("#operation-kicker").textContent = titles[kind][0];
  document.querySelector("#operation-title").textContent = titles[kind][1];
  try {
    if (kind === "job") {
      const users = await operationApi("/v1/users");
      document.querySelector("#job-manager").innerHTML = '<option value="">暂不指定</option>' + selectOptions(users, (user) => `${user.display_name} · ${user.role}`);
    }
    if (kind === "schedule") {
      const [candidates, users, jobs] = await Promise.all([operationApi("/v1/candidates"), operationApi("/v1/users"), operationApi("/v1/jobs")]);
      if (!candidates.length || !users.length || !jobs.length) throw new Error("请先至少创建一位候选人、一个职位和一位面试官。");
      document.querySelector("#schedule-candidate").innerHTML = selectOptions(candidates, (candidate) => `${candidate.full_name} · ${candidate.email}`);
      document.querySelector("#schedule-interviewer").innerHTML = selectOptions(users, (user) => `${user.display_name} · ${user.role}`);
      document.querySelector("#schedule-job").innerHTML = selectOptions(jobs, (job) => `${job.title} · ${job.status}`);
    }
    operationDialog.showModal();
  } catch (error) { operationToast(error.message); }
}

async function submitOperation(successMessage, path, payload) {
  clearOperationError();
  try {
    await operationApi(path, { method: "POST", body: JSON.stringify(payload) });
    operationDialog.close();
    await loadOverview();
    operationToast(successMessage);
  } catch (error) {
    const message = error.status === 409 ? "排期冲突：该面试官在此时段已有面试。推荐时段不等于预占，PostgreSQL 已拒绝重叠写入（409 Conflict）。" : error.message;
    setOperationError(message);
  }
}

document.querySelectorAll(".quick-action").forEach((button) => button.addEventListener("click", () => openOperation(button.dataset.operation)));
document.querySelector("#job-submit").addEventListener("click", () => submitOperation("职位已创建", "/v1/jobs", { title: document.querySelector("#job-title").value.trim(), hiring_manager_id: document.querySelector("#job-manager").value || null }));
document.querySelector("#candidate-submit").addEventListener("click", () => submitOperation("候选人已录入", "/v1/candidates", { full_name: document.querySelector("#candidate-name").value.trim(), email: document.querySelector("#candidate-email").value.trim(), phone: document.querySelector("#candidate-phone").value.trim() || null }));
document.querySelector("#schedule-submit").addEventListener("click", () => submitOperation("面试已安排，通知正在异步投递", "/v1/interviews", { candidate_id: document.querySelector("#schedule-candidate").value, interviewer_id: document.querySelector("#schedule-interviewer").value, job_id: document.querySelector("#schedule-job").value, starts_at: new Date(document.querySelector("#schedule-start").value).toISOString(), ends_at: new Date(document.querySelector("#schedule-end").value).toISOString(), meeting_url: document.querySelector("#schedule-url").value.trim() || null }));
