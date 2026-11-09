const tokenKey = "hireflow.access_token";
const dialog = document.querySelector("#auth-dialog");
const errorBox = document.querySelector("#error");
const toast = document.querySelector("#toast");
const token = () => localStorage.getItem(tokenKey);
const filePreview = window.location.protocol === "file:";
let refreshPromise = null;

if (filePreview) {
  document.body.classList.add("file-preview");
  document.querySelectorAll('a[href="/docs"]').forEach((link) => { link.href = "http://localhost:8000/docs"; });
  document.querySelectorAll('a[href="/metrics"]').forEach((link) => { link.href = "http://localhost:8000/metrics"; });
}

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

function cookieValue(name) {
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie.split("; ").find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : null;
}

function clearSession() {
  localStorage.removeItem(tokenKey);
  document.cookie = "hireflow_csrf=; Max-Age=0; Path=/; SameSite=Strict";
  document.querySelector("#session-actions")?.classList.add("hidden");
  document.querySelector("#open-auth").innerHTML = "连接团队 <span>→</span>";
}

async function refreshSession() {
  if (!cookieValue("hireflow_csrf")) return false;
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const response = await fetch("/v1/auth/refresh", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRF-Token": cookieValue("hireflow_csrf") || "" },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.access_token) {
        clearSession();
        return false;
      }
      localStorage.setItem(tokenKey, data.access_token);
      return true;
    })().finally(() => { refreshPromise = null; });
  }
  return refreshPromise;
}

async function api(path, options = {}, allowRefresh = true) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token()) headers.Authorization = `Bearer ${token()}`;
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  if (response.status === 401 && allowRefresh && token() && !path.startsWith("/v1/auth/")) {
    if (await refreshSession()) return api(path, options, false);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || `请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function fmtTime(value) {
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function renderOverview(data) {
  document.querySelector("#open-jobs").textContent = data.open_jobs;
  document.querySelector("#candidates").textContent = data.candidates;
  document.querySelector("#upcoming").textContent = data.upcoming_interviews;
  document.querySelector("#unread-notifications").textContent = data.unread_notifications;
}

async function loadOverview() {
  if (!token() && !(await refreshSession())) return;
  try {
    renderOverview(await api("/v1/dashboard/overview"));
    document.querySelector("#open-auth").innerHTML = "已连接 <span>✓</span>";
    document.querySelector("#session-actions")?.classList.remove("hidden");
  } catch (error) {
    clearSession();
    showToast("登录已过期，请重新连接团队");
  }
}

function openAuth() {
  if (filePreview) {
    showToast("静态预览不可连接后端，请访问 localhost:8000");
    return;
  }
  clearError();
  dialog.showModal();
}
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
    const response = await api(action === "login" ? "/v1/auth/tokens" : "/v1/auth/bootstrap", {
      method: "POST",
      headers: { "X-HireFlow-Client": "browser" },
      body: JSON.stringify(payload),
    });
    localStorage.setItem(tokenKey, response.access_token);
    dialog.close();
    await loadOverview();
    showToast(action === "login" ? "已连接到招聘团队" : "演示团队创建成功");
  } catch (error) { showError(error.message); }
}

document.querySelector("#login-submit").addEventListener("click", () => connect("login"));
document.querySelector("#bootstrap-submit").addEventListener("click", () => connect("bootstrap"));
document.querySelector("#logout-submit").addEventListener("click", async () => {
  try {
    await api("/v1/auth/logout", {
      method: "POST",
      headers: { "X-CSRF-Token": cookieValue("hireflow_csrf") || "" },
    }, false);
  } catch (_) {
    // Local state must still be cleared when the server-side session already expired.
  }
  clearSession();
  dialog.close();
  showToast("已安全退出当前会话");
});
loadOverview();
