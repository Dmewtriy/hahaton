import { api } from "./api.js";
import {
  e,
  icon,
  initials,
  roleName,
  notify,
  confirmAction,
  drawer,
} from "./components.js";
import { overview, accessPage, settingsPage } from "./views-overview.js";
import {
  employeesPage,
  branchesPage,
  departmentsPage,
  employeeEditor,
  employeeDetail,
} from "./views-people.js";
import {
  documentsPage,
  surveysPage,
  scenariosPage,
  surveyEditor,
} from "./views-content.js";
import {
  campaignsPage,
  campaignEditor,
  campaignDetail,
} from "./views-campaigns.js";
import { ticketsPage, ticketDetail } from "./views-support.js";

const root = document.querySelector("#root");
const definitions = [
  ["overview", "Обзор", "home", overview],
  ["employees", "Сотрудники", "users", employeesPage],
  ["branches", "Площадки", "branch", branchesPage],
  ["departments", "Подразделения", "department", departmentsPage],
  ["documents", "Документы", "document", documentsPage],
  ["surveys", "Опросники", "survey", surveysPage],
  ["campaigns", "Рассылки", "send", campaignsPage],
  ["scenarios", "Адаптация", "scenario", scenariosPage],
  ["tickets", "Переписка HR", "ticket", ticketsPage],
  ["access", "Доступ и роли", "lock", accessPage],
  ["settings", "Настройки", "settings", settingsPage],
];
const ctx = {
  api,
  me: null,
  refs: {},
  states: {},
  view: "overview",
  notify,
  confirm: confirmAction,
  drawer,
  employeeEditor: (id) => employeeEditor(ctx, id),
  employeeDetail: (id) => employeeDetail(ctx, id),
  surveyEditor: (id) => surveyEditor(ctx, id),
  campaignEditor: (data) => campaignEditor(ctx, data),
  campaignDetail: (id) => campaignDetail(ctx, id),
  ticketDetail: (id) => ticketDetail(ctx, id),
};
let navigationId = 0,
  inboxTimer = null;
ctx.syncInbox = async (refresh = true) => {
  if (!ctx.me?.capabilities.manage_hr) return;
  if (refresh) await ctx.onThreadPoll?.();
  const data = await api.get("/api/admin/inbox/summary");
  const dot = root.querySelector("[data-inbox-badge]");
  if (dot) {
    dot.hidden = !data.unread_threads;
    dot.title = `Переписок с новыми сообщениями: ${data.unread_threads}`;
    dot.setAttribute("aria-label", dot.title);
  }
  if (refresh && ctx.view === "tickets") await ctx.onInboxPoll?.();
};
function startInboxPolling() {
  clearInterval(inboxTimer);
  ctx.syncInbox().catch(() => {});
  inboxTimer = setInterval(() => {
    if (!document.hidden) ctx.syncInbox().catch(() => {});
  }, 10000);
}

ctx.loadRefs = async () => {
  if (!ctx.me.capabilities.manage_hr) {
    ctx.refs = {};
    return;
  }
  const names = [
    "branches",
    "departments",
    "employees",
    "surveys",
    "scenarios",
    "documents",
  ];
  const values = await Promise.all(
    names.map((name) => api.get(`/api/admin/${name}`)),
  );
  names.forEach((name, i) => (ctx.refs[name] = values[i]));
  ctx.refs.staff = await api.get("/api/staff-directory");
};
ctx.refresh = async () => {
  await ctx.loadRefs();
  await navigate(ctx.view);
};
ctx.navigate = async (view) => navigate(view);

function shell() {
  const allowed = definitions.filter(
    ([name]) =>
      name === "overview" ||
      (name === "access"
        ? ctx.me.capabilities.manage_people
        : name === "settings"
          ? ctx.me.capabilities.manage_hr || ctx.me.capabilities.manage_access
          : ctx.me.capabilities.manage_hr),
  );
  root.innerHTML = `<div class="shell"><aside class="sidebar"><div class="brand"><span class="brand-mark">К</span><div><strong>Корсик</strong><small>Рабочее пространство HR</small></div></div><div class="nav-group eyebrow">Управление</div><nav aria-label="Основная навигация">${allowed.map(([name, label, symbol]) => `<button class="nav-btn" data-view="${name}">${icon(symbol)}<span>${label}</span>${name === "tickets" ? '<span class="inbox-nav-dot" data-inbox-badge hidden role="img" aria-label="Новые сообщения HR"></span>' : ""}</button>`).join("")}</nav><div class="nav-bottom"><div class="nav-scope">Область управления<strong>${e(ctx.me.branch_codes.includes("*") ? "Все площадки компании" : ctx.me.branch_codes.join(", ") || "Управление доступом")}</strong></div><button class="nav-btn" data-logout>${icon("logout")}Выйти</button></div></aside><div class="workspace"><header class="topbar"><div class="actions"><button class="icon-btn mobile-menu" data-menu aria-label="Открыть навигацию">${icon("menu")}</button><div class="breadcrumb">Рабочее пространство <span> / </span><strong id="current-section">Обзор</strong></div></div><div class="user-info"><span class="avatar">${e(initials(ctx.me.name))}</span><div><strong>${e(ctx.me.name)}</strong><small>${e(ctx.me.roles.map(roleName).join(", ") || "Администратор доступа")}</small></div></div></header><main id="content" class="content" tabindex="-1"></main></div></div>`;
  root
    .querySelectorAll("[data-view]")
    .forEach((b) => (b.onclick = () => navigate(b.dataset.view)));
  root.querySelector("[data-menu]").onclick = () =>
    root.querySelector(".shell").classList.toggle("nav-open");
  root.querySelector("[data-logout]").onclick = async () => {
    try {
      await api.post("/api/admin/auth/logout");
    } catch {}
    api.setToken(null);
    ctx.me = null;
    clearInterval(inboxTimer);
    ctx.onThreadPoll = null;
    ctx.onInboxPoll = null;
    ctx.refs = {};
    document.querySelector("#drawer").close();
    loginScreen("Вы вышли из кабинета");
  };
}

async function navigate(view) {
  const definition = definitions.find((d) => d[0] === view);
  if (!definition) return;
  const target = root.querySelector("#content");
  if (!target) return;
  const request = ++navigationId;
  ctx.view = view;
  ctx.onInboxPoll = null;
  root.querySelector(".shell").classList.remove("nav-open");
  root
    .querySelectorAll("[data-view]")
    .forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  root.querySelector("#current-section").textContent = definition[1];
  target.innerHTML =
    '<div class="loading" role="status">Загружаем данные…</div>';
  try {
    const screen = await definition[3](ctx);
    if (request !== navigationId) return;
    target.innerHTML = screen.html;
    screen.bind?.(target);
  } catch (error) {
    if (request === navigationId)
      target.innerHTML = `<div class="card error-screen"><h2>Не удалось открыть раздел</h2><p>${e(error.message)}</p><button class="btn" id="retry-page">Попробовать снова</button></div>`;
    target
      .querySelector("#retry-page")
      ?.addEventListener("click", () => navigate(view));
  }
}

async function authenticated(token) {
  api.setToken(token);
  ctx.me = await api.get("/api/me");
  if (!ctx.me.is_admin) {
    api.setToken(null);
    throw new Error("У вашего аккаунта нет административного доступа");
  }
  await ctx.loadRefs();
  shell();
  await navigate("overview");
  startInboxPolling();
}

async function loginScreen(message = "") {
  let config;
  try {
    config = await api.get("/api/admin/config");
  } catch {
    config = { local_login: false };
  }
  root.innerHTML = `<div class="login-screen"><section class="login-brand"><div class="brand"><span class="brand-mark">К</span><strong>Корсик</strong></div><div><div class="eyebrow">Люди. Поддержка. Развитие.</div><h1>Хорошее начало<br>для каждого сотрудника.</h1><p>Всё для адаптации команды: сотрудники, знания, опросы и своевременная помощь HR.</p></div><footer>Кабинет администратора · ПОЛИПЛАСТИК</footer></section><section class="login-form-wrap"><div class="login-form"><h2>Кабинет HR</h2><p>${config.local_login ? "В MAX кабинет открывается автоматически. Для локальной работы войдите административным ключом." : "Откройте кабинет из бота MAX под аккаунтом HR или администратора."}</p>${config.local_login ? '<form id="local-login"><div class="field"><label for="admin-key">Ключ доступа</label><input id="admin-key" name="api_key" type="password" autocomplete="off" required placeholder="Вставьте ключ HR или администратора"></div><div class="form-error" role="alert"></div><button class="btn primary" type="submit">Войти в кабинет</button></form>' : ""}<div id="login-message" class="${message ? "notice warning" : ""}">${e(message)}</div><footer>Сотрудникам и руководителям без административных прав кабинет недоступен. Ключ остаётся только в памяти этой вкладки.</footer></div></section></div>`;
  root
    .querySelector("#local-login")
    ?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.target,
        button = form.querySelector("button");
      button.disabled = true;
      try {
        const result = await api.post("/api/admin/auth/local", {
          api_key: form.elements.api_key.value,
        });
        await authenticated(result.session_token);
      } catch (error) {
        form.querySelector(".form-error").textContent = error.message;
      } finally {
        button.disabled = false;
      }
    });
}

async function boot() {
  const raw =
    window.WebApp?.initData ||
    (location.hash.includes("WebAppData=") ? location.hash : "");
  if (raw) {
    try {
      const result = await api.post("/api/admin/auth/max", { init_data: raw });
      await authenticated(result.session_token);
    } catch (error) {
      root.innerHTML = `<div class="boot error-screen"><span class="brand-mark">К</span><h2>Кабинет недоступен</h2><p>${e(error.message)}</p><p>Если доступ должен быть, обратитесь к администратору системы.</p></div>`;
    }
  } else await loginScreen();
}
window.addEventListener("session-expired", () => {
  api.setToken(null);
  ctx.me = null;
  clearInterval(inboxTimer);
  ctx.onThreadPoll = null;
  ctx.onInboxPoll = null;
  ctx.refs = {};
  document.querySelector("#drawer").close();
  loginScreen("Сеанс завершён. Войдите заново");
});
boot();
