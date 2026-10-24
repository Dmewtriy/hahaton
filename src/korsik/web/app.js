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
import {
  teamPage,
  meetingsPage,
  digestsPage,
  managerRulesPage,
} from "./views-role.js";

import { reportsPage, materialsPage, processingPage } from "./views-tools.js";
import { attachmentsPage, planningPage, managementPage, improvementsPage, grantsPage } from "./views-extensions.js";
const root = document.querySelector("#root");
const definitions = [
  ["attachments", "Планы и вложения", "document", attachmentsPage],
  ["planning", "Мероприятия и встречи", "clock", planningPage],
  ["management", "Поручения", "ticket", managementPage],
  ["improvements", "Что улучшить", "document", improvementsPage],
  ["grants", "Полномочия на поручения", "lock", grantsPage],
  ["overview", "Обзор", "home", overview],
  ["employees", "Сотрудники", "users", employeesPage],
  ["branches", "Площадки", "branch", branchesPage],
  ["departments", "Подразделения", "department", departmentsPage],
  ["documents", "Документы", "document", documentsPage],
  ["surveys", "Опросники", "survey", surveysPage],
  ["reports", "Выгрузки", "document", reportsPage],
  ["materials", "Материалы маршрута", "document", materialsPage],
  ["processing", "Обработка", "clock", processingPage],
  ["campaigns", "Рассылки", "send", campaignsPage],
  ["scenarios", "Адаптация", "scenario", scenariosPage],
  ["tickets", "Переписка HR", "ticket", ticketsPage],
  ["access", "Доступ и роли", "lock", accessPage],
  ["settings", "Настройки", "settings", settingsPage],
  ["team", "Моя команда", "users", teamPage],
  ["meetings", "Личные встречи", "users", meetingsPage],
  ["digests", "Недельные сводки", "document", digestsPage],
  ["manager-rules", "Назначение руководителей", "department", managerRulesPage],
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
  if (ctx.me.features?.management) {
    const management = await api.get("/api/management/summary");
    const dot = root.querySelector("[data-management-badge]");
    if (dot) { dot.hidden = !management.unread_count; dot.textContent=management.unread_count; dot.setAttribute("aria-label","Новых событий в поручениях: "+management.unread_count); }
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
  ctx.refs.reference = await api.get("/api/admin/reference-data");
};
ctx.refresh = async () => {
  await ctx.loadRefs();
  await navigate(ctx.view);
};
ctx.navigate = async (view) => navigate(view);

function shell() {
  const allowed = definitions.filter(([name]) => {
    if (["attachments","planning"].includes(name)) return ctx.me.features?.optional && ctx.me.capabilities.manage_hr;
    if (name === "management") return ctx.me.features?.management && ctx.me.capabilities.manage_hr;
    if (name === "improvements") return ctx.me.features?.management && ctx.me.roles.includes("hr_head");
    if (name === "grants") return ctx.me.features?.management && ctx.me.manage_access;
    return true;
  }).filter(
    ([name]) =>
      (name === "overview" && ctx.me.is_admin) ||
      (name === "team" && ctx.me.capabilities.view_team) ||
      (name === "meetings" &&
        (ctx.me.capabilities.view_team || ctx.me.capabilities.manage_hr)) ||
      (name === "digests" && ctx.me.roles.includes("hr_head")) ||
      (name === "access"
        ? ctx.me.capabilities.manage_people
        : name === "settings"
          ? ctx.me.capabilities.manage_hr || ctx.me.capabilities.manage_access
          : !["team", "meetings", "digests"].includes(name) &&
            ctx.me.capabilities.manage_hr),
  );
  const groups = [
    ["Главное", ["overview"]],
    ["Сотрудники и помощь", ["employees", "tickets", "management", "team"]],
    ["Опросы и аналитика", ["surveys", "campaigns", "reports", "improvements", "digests", "processing"]],
    ["Адаптация и материалы", ["scenarios", "documents", "attachments", "materials"]],
    ["Планирование", ["planning", "meetings"]],
    ["Управление", ["branches", "departments", "manager-rules", "grants", "access", "settings"]],
  ];
  const navigation = groups.map(([title, names], index) => {
    const entries = allowed.filter(([name]) => names.includes(name));
    if (!entries.length) return "";
    return '<details class="nav-section" ' + (index < 2 ? "open" : "") + '><summary>' + e(title) + '</summary>' + entries.map(([name,label,symbol]) => '<button class="nav-btn" data-view="' + e(name) + '">' + icon(symbol) + '<span>' + e(name === "meetings" ? "Рекомендации встреч" : label) + '</span>' + (name === "tickets" ? '<span class="inbox-nav-dot" data-inbox-badge hidden aria-label="Новые сообщения HR"></span>' : name === "management" ? '<span class="inbox-nav-dot" data-management-badge hidden></span>' : "") + '</button>').join("") + '</details>';
  }).join("");
  root.innerHTML = `<div class="shell"><aside class="sidebar"><div class="brand"><img class="brand-mascot" src="/admin-assets/brand/korsik-14.jpg" alt="Корсик"><div><strong>Корсик</strong><small>${ctx.me.is_admin ? "Рабочее пространство HR" : "Моя команда"}</small></div></div><button class="nav-btn" data-main-menu>☰ Меню</button><label class="nav-search"><span class="sr-only">Найти раздел</span><input type="search" placeholder="Найти раздел…" data-nav-search></label><nav aria-label="Основная навигация">${navigation}</nav><div class="nav-bottom"><div class="nav-scope">Область управления<strong>${e(ctx.me.branch_codes.includes("*") ? "Все площадки компании" : ctx.me.branch_codes.join(", ") || "Управление доступом")}</strong></div><button class="nav-btn" data-logout>${icon("logout")}Выйти</button></div></aside><div class="workspace"><header class="topbar"><div class="actions"><button class="icon-btn mobile-menu" data-menu aria-label="Открыть навигацию">${icon("menu")}</button><div class="breadcrumb">Рабочее пространство <span> / </span><strong id="current-section">Обзор</strong></div></div><div class="user-info"><span class="avatar">${e(initials(ctx.me.name))}</span><div><strong>${e(ctx.me.name)}</strong><small>${e(ctx.me.roles.map(roleName).join(", ") || "Администратор доступа")}</small></div></div></header><main id="content" class="content" tabindex="-1"></main></div></div>`;
  root
    .querySelectorAll("[data-view]")
    .forEach((b) => (b.onclick = () => navigate(b.dataset.view)));
  root.querySelector("[data-nav-search]").oninput = (event) => {
    const query = event.target.value.trim().toLocaleLowerCase("ru");
    root.querySelectorAll(".nav-section").forEach(group => {
      let visible = 0;
      group.querySelectorAll("[data-view]").forEach(button => {
        button.hidden = !button.textContent.toLocaleLowerCase("ru").includes(query);
        if (!button.hidden) visible++;
      });
      group.hidden = !visible;
      if (query) group.open = true;
    });
  };
  root.querySelector("[data-main-menu]").onclick = async () => {
    try {
      const actions = await api.get("/api/menu");
      drawer("Меню Корсика", '<div class="actions">' + allowed.map(([id,label]) => '<button type="button" class="btn" data-section="' + e(id) + '">' + e(label) + '</button>').join("") + '</div><h3>Действия в боте MAX</h3><div class="actions">' + actions.filter(a => !["menu","help","admin"].includes(a.id)).map(a=>'<button type="button" class="btn" data-bot-action="'+e(a.id)+'">'+e(a.label)+'</button>').join("")+'</div>', {bind(form) {
        form.querySelectorAll("[data-section]").forEach(b=>b.onclick=()=>{document.querySelector("#drawer").close();navigate(b.dataset.section);});
        form.querySelectorAll("[data-bot-action]").forEach(b=>b.onclick=async()=>{try{await api.post("/api/menu/"+b.dataset.botAction);notify("Ответ придёт в MAX");}catch(err){notify(err.message,true);}});
      }});
    } catch(err) { notify(err.message,true); }
  };
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
    .forEach((b) => {
      b.classList.toggle("active", b.dataset.view === view);
      b.setAttribute("aria-current", b.dataset.view === view ? "page" : "false");
      if (b.dataset.view === view) b.closest("details").open = true;
    });
  root.querySelector("#current-section").textContent = definition[1];
  target.innerHTML =
    '<div class="loading" role="status">Загружаем данные…</div>';
  try {
    const screen = await (view === "overview" && !ctx.me.is_admin
      ? teamPage(ctx)
      : definition[3](ctx));
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
  if (!ctx.me.can_use_workspace) {
    api.setToken(null);
    throw new Error("У вашего аккаунта нет административного доступа");
  }
  await ctx.loadRefs();
  shell();
  await navigate(
    ctx.me.capabilities.manage_hr
      ? "overview"
      : ctx.me.capabilities.view_team
        ? "team"
        : "access",
  );
  startInboxPolling();
}

async function loginScreen(message = "") {
  let config;
  try {
    config = await api.get("/api/admin/config");
  } catch {
    config = { local_login: false };
  }
  root.innerHTML = `<div class="login-screen"><section class="login-brand"><div class="brand"><span class="brand-mark">К</span><strong>Корсик</strong></div><div><div class="eyebrow">Люди. Поддержка. Развитие.</div><img class="login-mascot" src="/admin-assets/brand/korsik-04.jpg" alt="Корсик: добро пожаловать в команду"><h1>Хорошее начало<br>для каждого сотрудника.</h1><p>Всё для адаптации команды: сотрудники, знания, опросы и своевременная помощь HR.</p></div><footer>Кабинет администратора · ПОЛИПЛАСТИК</footer></section><section class="login-form-wrap"><div class="login-form"><h2>Кабинет HR</h2><p>${config.local_login ? "В MAX кабинет открывается автоматически. Для локальной работы войдите ключом своего аккаунта." : "Откройте кабинет из бота MAX под аккаунтом HR, руководителя или администратора."}</p>${config.local_login ? '<form id="local-login"><div class="field"><label for="admin-key">Ключ доступа</label><input id="admin-key" name="api_key" type="password" autocomplete="off" required placeholder="Вставьте ключ HR или администратора"></div><div class="form-error" role="alert"></div><button class="btn primary" type="submit">Войти в кабинет</button></form>' : ""}<div id="login-message" class="${message ? "notice warning" : ""}">${e(message)}</div><footer>Сотрудники работают в чате. Руководителям доступен только просмотр своей команды. Ключ остаётся только в памяти этой вкладки.</footer></div></section></div>`;
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
