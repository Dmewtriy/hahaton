export const e = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const paths = {
  home: '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1Z"/><path d="M9 21v-8h6v8"/>',
  users:
    '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M22 21v-2a4 4 0 0 0-3-3.87"/><circle cx="9" cy="7" r="4"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  branch:
    '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M9 7h1m4 0h1m-6 4h1m4 0h1m-6 4h1m4 0h1m-5 6v-3h4v3"/>',
  department:
    '<rect x="8" y="3" width="8" height="5" rx="1"/><rect x="2" y="16" width="7" height="5" rx="1"/><rect x="15" y="16" width="7" height="5" rx="1"/><path d="M12 8v4M5 16v-4h14v4"/>',
  document:
    '<path d="M14 2H5a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9Z"/><path d="M14 2v7h7M7 13h10M7 17h7"/>',
  survey:
    '<rect x="5" y="4" width="14" height="18" rx="2"/><rect x="9" y="2" width="6" height="4" rx="1"/><path d="m9 11 1 1 2-2m2 1h2m-7 6h7"/>',
  scenario:
    '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="12" r="2"/><circle cx="6" cy="19" r="2"/><path d="M8 5h4a6 6 0 0 1 6 5M8 19h4a6 6 0 0 0 6-5M6 7v10"/>',
  send: '<path d="m22 2-7 20-4-9L2 9Z"/><path d="M22 2 11 13"/>',
  ticket:
    '<path d="M21 11a8 8 0 0 1-8 8H7l-5 3V11a9 9 0 0 1 19 0Z"/><path d="M7 10h10m-10 4h6"/>',
  settings:
    '<path d="M9.5 3h5l.6 2.2 2 .9 2-.8 2.4 4.2-1.7 1.5v2l1.7 1.5-2.4 4.2-2-.8-2 .9-.6 2.2h-5l-.6-2.2-2-.9-2 .8-2.4-4.2 1.7-1.5v-2L2.5 9.5l2.4-4.2 2 .8 2-.9Z"/><circle cx="12" cy="12" r="3"/>',
  lock: '<rect x="4" y="10" width="16" height="12" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4m-4 5v3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="10.5" cy="10.5" r="7"/><path d="m16 16 5 5"/>',
  edit: '<path d="m16 3 5 5-12 12-6 1 1-6Z"/><path d="m13 6 5 5"/>',
  trash: '<path d="M3 6h18m-16 0 1 15h12l1-15M9 6V3h6v3m-5 4v7m4-7v7"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  up: '<path d="m6 14 6-6 6 6"/>',
  down: '<path d="m6 10 6 6 6-6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
  logout: '<path d="M9 3H3v18h6m7-14 5 5-5 5M8 12h13"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  restore: '<path d="M3 11a9 9 0 1 1 2 7M3 5v6h6m3-4v5l3 2"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
};
export const icon = (name) =>
  `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.info}</svg>`;
export const button = (label, action, id = "", type = "") =>
  `<button type="button" class="btn ${e(type)}" data-action="${e(action)}" data-id="${e(id)}">${e(label)}</button>`;
export const iconButton = (name, title, action, id = "") =>
  `<button type="button" class="icon-btn" title="${e(title)}" aria-label="${e(title)}" data-action="${e(action)}" data-id="${e(id)}">${icon(name)}</button>`;
export const initials = (name) =>
  String(name)
    .replace(/[^\p{L}\p{N}\s]/gu, "")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((x) => x[0])
    .join("")
    .toUpperCase();
export const date = (value) =>
  value
    ? new Intl.DateTimeFormat("ru", {
        day: "numeric",
        month: "short",
        year: "numeric",
      }).format(new Date(value))
    : "—";
export const dateTime = (value) =>
  value
    ? new Intl.DateTimeFormat("ru", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(value))
    : "—";
const statuses = {
  active: ["Активен", "green"],
  invited: ["Ждёт подключения", "orange"],
  stopped: ["Отключён", ""],
  pending: ["В очереди", "orange"],
  enqueued: ["Запланирован", "blue"],
  queued: ["Ожидает диалог", "orange"],
  dispatched: ["Передан на отправку", "blue"],
  completed: ["Завершён", "green"],
  manual: ["Ручной запуск", "purple"],
  cancelled: ["Отменён", ""],
  draft: ["Черновик", ""],
  scheduled: ["Запланирована", "blue"],
  sending: ["Выполняется", "orange"],
  sent: ["Отправлено", "green"],
  failed: ["Ошибка", "red"],
  uncertain: ["Проверить доставку", "red"],
  ready: ["Готов", "green"],
  ready_stub: ["Заглушка ML", "purple"],
  processing: ["Индексируется", "blue"],
  open: ["Новое", "orange"],
  in_progress: ["В работе", "blue"],
  closed: ["Закрыто", "green"],
  retry: ["Повтор", "orange"],
};
export const badge = (value) =>
  `<span class="badge ${statuses[value]?.[1] || ""}">${e(statuses[value]?.[0] || value)}</span>`;
export const roleName = (role) =>
  ({
    employee: "Сотрудник",
    hr: "HR-администратор",
    manager: "Руководитель подразделения",
    hr_head: "Руководитель HR",
  })[role] || role;
export function empty(title, text, action = "", label = "") {
  return `<div class="empty"><div class="empty-icon">${icon("document")}</div><h3>${e(title)}</h3><p>${e(text)}</p>${action ? button(label, action, "", "primary") : ""}</div>`;
}
export function heading(title, subtitle, actions = "") {
  return `<div class="page-heading"><div><h1>${e(title)}</h1><p>${e(subtitle)}</p></div><div class="actions">${actions}</div></div>`;
}
let counter = 0;
export function field(name, label, control, hint = "") {
  const id = `field-${++counter}`;
  return `<div class="field"><label for="${id}">${e(label)}</label>${control.replace(/<(input|select|textarea)\b/, `<$1 id="${id}" name="${e(name)}"`)}${hint ? `<small>${e(hint)}</small>` : ""}</div>`;
}
export const input = (
  name,
  label,
  value = "",
  type = "text",
  attrs = "",
  hint = "",
) =>
  field(
    name,
    label,
    `<input type="${type}" value="${e(value)}" ${attrs}>`,
    hint,
  );
export const textarea = (name, label, value = "", attrs = "") =>
  field(name, label, `<textarea ${attrs}>${e(value)}</textarea>`);
export const select = (
  name,
  label,
  options,
  value = "",
  placeholder = "Выберите…",
) =>
  field(
    name,
    label,
    `<select>${placeholder !== null ? `<option value="">${e(placeholder)}</option>` : ""}${options.map((o) => `<option value="${e(o.value)}" ${String(o.value) === String(value) ? "selected" : ""}>${e(o.label)}</option>`).join("")}</select>`,
  );
export const checkbox = (name, label, checked = false, value = "yes") =>
  `<label class="check"><input type="checkbox" name="${e(name)}" value="${e(value)}" ${checked ? "checked" : ""}>${e(label)}</label>`;
export const csv = (value) =>
  String(value || "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
export const formObject = (form) => Object.fromEntries(new FormData(form));
export function scopes(ctx, selected = null) {
  const values = selected || ctx.me.branch_codes;
  const branches = ctx.refs.branches;
  return `<div class="field"><span class="field-label">Доступно площадкам</span><div class="scope-grid">${ctx.me.branch_codes.includes("*") ? checkbox("branch_codes", "Все площадки", values.includes("*"), "*") : ""}${branches.map((b) => checkbox("branch_codes", b.name, values.includes(b.code), b.code)).join("")}</div></div>`;
}
export function readScopes(form) {
  const values = new FormData(form).getAll("branch_codes");
  if (!values.length) throw new Error("Выберите хотя бы одну площадку");
  return values.includes("*") ? ["*"] : values;
}
export function notify(message, error = false) {
  const element = document.createElement("div");
  element.className = "toast" + (error ? " error" : "");
  element.textContent = message;
  document.querySelector("#toasts").append(element);
  setTimeout(() => element.remove(), 5000);
}
export function confirmAction(
  title,
  text,
  label = "Продолжить",
  danger = false,
) {
  const dialog = document.querySelector("#confirm");
  dialog.innerHTML = `<h2 id="confirm-title">${e(title)}</h2><p>${e(text)}</p><footer><button class="btn" data-no>Отмена</button><button class="btn ${danger ? "danger" : "primary"}" data-yes>${e(label)}</button></footer>`;
  dialog.showModal();
  return new Promise((resolve) => {
    let resolved = false;
    const done = (value) => {
      if (resolved) return;
      resolved = true;
      dialog.close();
      resolve(value);
    };
    dialog.querySelector("[data-no]").onclick = () => done(false);
    dialog.querySelector("[data-yes]").onclick = () => done(true);
    dialog.oncancel = (event) => {
      event.preventDefault();
      done(false);
    };
  });
}
let dirty = false;
export function drawer(
  title,
  body,
  {
    subtitle = "",
    submit = null,
    submitLabel = "Сохранить",
    bind = null,
    closeOnSubmit = true,
  } = {},
) {
  const dialog = document.querySelector("#drawer");
  dirty = false;
  dialog.innerHTML = `<form id="editor-form"><div class="drawer-head"><div><h2 id="drawer-title">${e(title)}</h2>${subtitle ? `<small>${e(subtitle)}</small>` : ""}</div><button class="icon-btn" type="button" data-close aria-label="Закрыть">${icon("close")}</button></div><div class="drawer-body">${body}<div class="form-error" role="alert"></div></div><footer class="drawer-foot"><button class="btn" type="button" data-close>${submit ? "Отмена" : "Закрыть"}</button>${submit ? `<button class="btn primary" type="submit">${e(submitLabel)}</button>` : ""}</footer></form>`;
  const form = dialog.querySelector("form");
  const close = async () => {
    if (
      dirty &&
      !(await confirmAction(
        "Закрыть без сохранения?",
        "В форме остались несохранённые изменения.",
        "Закрыть",
      ))
    )
      return;
    dirty = false;
    dialog.close();
  };
  dialog.querySelectorAll("[data-close]").forEach((b) => (b.onclick = close));
  dialog.oncancel = (event) => {
    event.preventDefault();
    close();
  };
  form.oninput = () => {
    dirty = true;
  };
  form.onchange = () => {
    dirty = true;
  };
  if (submit)
    form.addEventListener("click", (event) => {
      const action = event.target.closest("[data-action]");
      if (
        action &&
        action.dataset.action !== "preview" &&
        action.dataset.action !== "audience-preview"
      )
        dirty = true;
    });
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (!submit) return;
    const button = form.querySelector("[type=submit]"),
      error = form.querySelector(".form-error");
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Сохраняем…";
    error.textContent = "";
    try {
      const afterSave = await submit(form);
      dirty = false;
      if (closeOnSubmit && dialog.open) dialog.close();
      if (typeof afterSave === "function") await afterSave();
    } catch (reason) {
      error.textContent = reason.message;
      error.scrollIntoView({ block: "nearest" });
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  };
  if (bind) bind(form);
  if (!dialog.open) dialog.showModal();
  return form;
}
window.addEventListener("beforeunload", (event) => {
  if (dirty) {
    event.preventDefault();
    event.returnValue = "";
  }
});
export function toolbar(searchPlaceholder, branches = [], value = {}) {
  return `<div class="toolbar"><div class="filters"><div class="search-box">${icon("search")}<input aria-label="Поиск" data-search placeholder="${e(searchPlaceholder)}" value="${e(value.search || "")}"></div>${branches.length ? `<select class="filter-select" data-branch aria-label="Фильтр по площадке"><option value="">Все площадки</option>${branches.map((b) => `<option value="${e(b.code)}" ${value.branch === b.code ? "selected" : ""}>${e(b.name)}</option>`).join("")}</select>` : ""}</div><div class="segmented"><button data-archived="0" class="${!value.archived ? "active" : ""}">Активные</button><button data-archived="1" class="${value.archived ? "active" : ""}">Архив</button></div></div>`;
}
export function bindFilters(container, state, rerender, reload) {
  container
    .querySelector("[data-search]")
    ?.addEventListener("input", (event) => {
      state.search = event.target.value;
      state.page = 0;
      rerender();
    });
  container
    .querySelector("[data-branch]")
    ?.addEventListener("change", (event) => {
      state.branch = event.target.value;
      state.page = 0;
      rerender();
    });
  container.querySelectorAll("[data-archived]").forEach(
    (button) =>
      (button.onclick = () => {
        state.archived = button.dataset.archived === "1";
        state.page = 0;
        reload();
      }),
  );
}
export function table(columns, rows, state, emptyHtml) {
  const size = 15,
    total = rows.length,
    pages = Math.max(1, Math.ceil(total / size));
  state.page = Math.min(state.page || 0, pages - 1);
  if (!total) return `<div class="card table-card">${emptyHtml}</div>`;
  return `<div class="card table-card"><div class="table-wrap"><table><thead><tr>${columns.map((c) => `<th>${e(c[0])}</th>`).join("")}</tr></thead><tbody>${rows
    .slice(state.page * size, (state.page + 1) * size)
    .map(
      (row) =>
        `<tr>${columns.map((c) => `<td>${c[1](row)}</td>`).join("")}</tr>`,
    )
    .join(
      "",
    )}</tbody></table></div><div class="pagination"><span>${state.page * size + 1}–${Math.min((state.page + 1) * size, total)} из ${total}</span><div class="actions"><button class="icon-btn" data-page="-1" ${!state.page ? "disabled" : ""} aria-label="Предыдущая страница">‹</button><span>${state.page + 1} / ${pages}</span><button class="icon-btn" data-page="1" ${state.page + 1 === pages ? "disabled" : ""} aria-label="Следующая страница">›</button></div></div></div>`;
}
export function delegate(container, handler) {
  container.onclick = async (event) => {
    const action = event.target.closest("[data-action]");
    if (!action || action.disabled) return;
    try {
      await handler(action.dataset.action, action.dataset.id, action);
    } catch (error) {
      notify(error.message, true);
    }
  };
}
export function bindPagination(container, state, render) {
  container.querySelectorAll("[data-page]").forEach(
    (b) =>
      (b.onclick = () => {
        state.page += Number(b.dataset.page);
        render();
      }),
  );
}
export async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
    notify("Скопировано");
  } catch {
    drawer("Скопируйте текст", `<div class="code">${e(value)}</div>`);
  }
}

const labelObserver = new MutationObserver(() => {
  document.querySelectorAll(".field label:not([for])").forEach((label) => {
    const control = label.nextElementSibling;
    if (control?.matches("input,textarea,select")) {
      control.id ||= `auto-field-${++counter}`;
      label.htmlFor = control.id;
    }
  });
});
labelObserver.observe(document.querySelector("#drawer"), {
  childList: true,
  subtree: true,
});
