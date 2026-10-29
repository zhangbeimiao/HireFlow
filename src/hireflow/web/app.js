const tokenKey = "hireflow.access_token";
const dialog = document.querySelector("#auth-dialog");
const errorBox = document.querySelector("#error");
const toast = document.querySelector("#toast");
const token = () => localStorage.getItem(tokenKey);

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 2600);
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.style.display = "block";
}

function clearError() { errorBox.style.display = "none"; }

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token()) headers.Authorization = `Bearer ${token()}`;
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`);
  return data;
}

function fmtTime(value) {
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function renderOverview(data) {
  document.querySelector("#open-jobs").textContent = data.open_jobs;
  document.querySelector("#candidates").textContent = data.candidates;
  document.querySelector("#upcoming").textContent = data.upcoming_interviews;
  document.querySelector("#notifications").textContent = data.unread_notifications;
  const list = document.querySelector("#interview-list");
  if (!data.upcoming.length) {
    list.className = "empty-state";
    list.innerHTML = "<span>◫</span><p>暂无待进行面试，先为候选人创建可用时段。</p>";
    return;
  }
  list.className = "";
  list.innerHTML = data.upcoming.map((item) => `<div class="interview-row"><span class="interview-time">${fmtTime(item.starts_at)}</span><div class="interview-main"><strong>${item.candidate_name}</strong><p>${item.job_title} · 面试官 ${item.interviewer_name}</p></div><span class="status-pill">${item.status}</span></div>`).join("");
}

async function loadOverview() {
  if (!token()) return;
  try {
    renderOverview(await api("/v1/dashboard/overview"));
    document.querySelector("#open-auth").innerHTML = "已连接 <span>✓</span>";
  } catch (error) {
    localStorage.removeItem(tokenKey);
    showToast("登录已过期，请重新连接团队");
  }
}

function openAuth() { clearError(); dialog.showModal(); }
document.querySelector("#open-auth").addEventListener("click", openAuth);
document.querySelector("#hero-auth").addEventListener("click", openAuth);
document.querySelector("#refresh").addEventListener("click", async () => { await loadOverview(); showToast("数据已刷新"); });

document.querySelectorAll(".tab").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab === button));
  document.querySelector("#login-view").classList.toggle("hidden", button.dataset.tab !== "login");
  document.querySelector("#bootstrap-view").classList.toggle("hidden", button.dataset.tab !== "bootstrap");
  clearError();
}));

async function connect(action) {
  clearError();
  try {
    const payload = action === "login" ? {
      tenant_id: document.querySelector("#login-tenant").value.trim(),
      email: document.querySelector("#login-email").value.trim(),
      password: document.querySelector("#login-password").value,
    } : {
      tenant_name: document.querySelector("#tenant-name").value.trim(),
      owner: { email: document.querySelector("#owner-email").value.trim(), display_name: document.querySelector("#owner-name").value.trim(), role: "owner", password: document.querySelector("#owner-password").value },
    };
    const response = await api(action === "login" ? "/v1/auth/tokens" : "/v1/auth/bootstrap", { method: "POST", body: JSON.stringify(payload) });
    localStorage.setItem(tokenKey, response.access_token);
    dialog.close();
    await loadOverview();
    showToast(action === "login" ? "已连接到招聘团队" : "演示团队创建成功");
  } catch (error) { showError(error.message); }
}

document.querySelector("#login-submit").addEventListener("click", () => connect("login"));
document.querySelector("#bootstrap-submit").addEventListener("click", () => connect("bootstrap"));
loadOverview();
