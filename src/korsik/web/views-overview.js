import { policyPanel, analyticsPanel } from "./views-policies.js";
import {
  e,
  icon,
  heading,
  button,
  badge,
  empty,
  delegate,
  dateTime,
  input,
  checkbox,
  csv,
  formObject,
  drawer,
  notify,
  roleName,
  copyText,
  scopes,
  readScopes,
} from "./components.js";

export async function overview(ctx) {
  const data = await ctx.api.get("/api/admin/overview");
  if (data.access_only)
    return {
      html:
        heading(
          "Управление доступом",
          "Назначайте рабочие роли и области ответственности.",
        ) +
        `<div class="card">${empty("Рабочие данные доступны HR", "Право управления доступом не открывает личные данные сотрудников.", "access", "Открыть аккаунты")}</div>`,
      bind: (container) => delegate(container, () => ctx.navigate("access")),
    };
  const totals = data.totals;
  const cards = [
    ["users", "Сотрудников в адаптации", totals.onboarding_employees, ""],
    ["survey", "Незавершённых опросов", totals.active_surveys, "purple"],
    ["ticket", "Открытых обращений", totals.open_tickets, "orange"],
    ["check", "Опросов за 7 дней", totals.completed_surveys, "green"],
  ];
  const phases = Object.entries(totals.stage_titles || totals.stages);
  return {
    html:
      heading(
        "Обзор адаптации",
        "Помогайте новичкам освоиться и вовремя замечайте, кому нужна поддержка.",
        button("Добавить сотрудника", "new-employee", "", "primary"),
      ) +
      (data.ml_mode === "stub"
        ? `<div class="notice">${icon("info")}<div><strong>Подключён демонстрационный ML-модуль.</strong> Опросы и обращения работают; ответы по документам и анализ настроения заменены учебными примерами.</div></div>`
        : "") +
      `<div class="kpis">${cards.map(([symbol, label, value, color]) => `<div class="card kpi"><div class="kpi-icon ${color}">${icon(symbol)}</div><strong>${value ?? 0}</strong><p>${label}</p></div>`).join("")}</div><div class="overview-grid"><div class="card"><div class="card-header"><h2>Требуют внимания</h2>${button("Все обращения", "tickets")}</div>${data.recent_tickets.length ? data.recent_tickets.map((t) => `<div class="card-row"><div><strong>${e(t.employee_name)}</strong><p>${e(t.reason)}</p></div><div class="actions">${badge(t.status)}${button("Открыть", "ticket", t.id)}</div></div>`).join("") : empty("Новых обращений нет", "Здесь появятся вопросы сотрудников и сигналы по обратной связи.")}</div><div class="stack"><div class="card"><div class="card-header"><h2>Этапы адаптации</h2></div>${phases.length ? phases.map(([name, count]) => `<div class="bar-row"><div><span>${e({ invited: "Ожидают подключения", preboarding: "До первого дня", welcome: "Первый день", equipment: "Рабочее место", day3: "Первые три дня", week2: "Две недели", month1: "Первый месяц", final: "Завершение" }[name] || name)}</span><strong>${count}</strong></div><div class="progress" style="--progress:${Math.round((count / Math.max(totals.employees, 1)) * 100)}%"><span></span></div></div>`).join("") : '<p class="muted">Добавьте первого сотрудника</p>'}</div><div class="card"><div class="card-header"><h2>База знаний</h2>${icon("document")}</div><div class="card-row"><span>Действующих документов</span><strong>${data.document_count}</strong></div><div class="card-row"><span>Площадок</span><strong>${data.branch_count}</strong></div><div class="spacer"></div>${button("Открыть документы", "documents")}</div></div></div><div class="page-footer">Числовые показатели рассчитаны из БД. Период: ${dateTime(data.window.from)} — ${dateTime(data.window.to)}.</div>` +
      analyticsPanel(data),
    bind: (container) =>
      delegate(container, async (action, id) => {
        if (action === "new-employee") await ctx.employeeEditor();
        else if (action === "ticket") await ctx.ticketDetail(id);
        else await ctx.navigate(action);
      }),
  };
}

export async function accessPage(ctx) {
  const data = (await ctx.api.get("/api/identities")).filter(
    (x) => x.id !== "bootstrap",
  );
  const trusted = ctx.me.capabilities.manage_access;
  const showKey = (key) =>
    drawer(
      "Ключ аккаунта",
      `<p class="muted">Сохраните ключ в защищённом месте. Повторно он не показывается.</p><div class="code">${e(key)}</div>`,
    );
  return {
    html:
      heading(
        "Доступ и роли",
        "Приглашайте HR и руководителей, назначайте роли и блокируйте доступ.",
        button("Создать аккаунт", "create", "", "primary"),
      ) +
      `<div class="notice">${icon("lock")}<div>HR управляет рабочими аккаунтами своей области. Доверенное право управления доступом выдаётся отдельно. Обычных сотрудников добавляйте в разделе «Сотрудники».</div></div><div class="card table-card"><div class="table-wrap"><table><thead><tr><th>Пользователь</th><th>Роли</th><th>Область</th><th>MAX</th><th></th></tr></thead><tbody>${data.map((i) => `<tr><td><strong>${e(i.name)}</strong><span class="cell-sub">${i.active ? "Доступ открыт" : "Заблокирован"}</span></td><td>${e(i.roles.map(roleName).join(", "))}${i.manage_access ? '<span class="cell-sub">Управляет доступом</span>' : ""}</td><td>${e(i.branch_codes.includes("*") ? "Все площадки" : i.branch_codes.map((code) => ctx.refs.branches?.find((b) => b.code === code)?.name || code).join(", "))}</td><td>${i.max_bound ? badge("active") : badge("invited")}</td><td><div class="actions">${button("Настроить", "edit", i.id)}${i.active && !i.max_bound ? button("Пригласить", "invite", i.id) : ""}</div></td></tr>`).join("")}</tbody></table></div></div>`,
    bind: (container) =>
      delegate(container, async (action, id) => {
        const item = data.find((x) => x.id === id);
        if (action === "invite") {
          const invite = await ctx.api.post(`/api/identities/${id}/invitation`);
          const text = invite.max_link || "/start " + invite.invitation_token;
          drawer(
            `Приглашение: ${item.name}`,
            `<p class="muted">Ссылка действует ${invite.expires_in_hours} часов и используется один раз.</p><div class="code">${e(text)}</div>${button("Скопировать", "copy")}`,
            { bind: (form) => delegate(form, () => copyText(text)) },
          );
          return;
        }
        const edit = action === "edit";
        const scopeFields = ctx.refs.branches
          ? scopes(ctx, item?.branch_codes)
          : input(
              "branches",
              "Коды площадок",
              item?.branch_codes.join(", ") || ctx.me.branch_codes.join(", "),
              "text",
              "",
              "* — все площадки. Несколько кодов разделяйте запятой.",
            );
        drawer(
          edit ? `Доступ: ${item.name}` : "Новый аккаунт",
          input(
            "name",
            "Имя пользователя",
            item?.name || "",
            "text",
            "required",
          ) +
            `<div class="field"><span class="field-label">Рабочие роли</span>${["employee", "hr", "manager", "hr_head"].map((r) => checkbox("roles", roleName(r), item?.roles.includes(r), r)).join("")}</div>` +
            input(
              "contact_url",
              "Личный контакт HR (ссылка)",
              item?.contact_url || "",
              "url",
            ) +
            scopeFields +
            (trusted
              ? checkbox(
                  "manage_access",
                  "Разрешить управление доступом",
                  item?.manage_access,
                )
              : "") +
            (edit ? checkbox("active", "Аккаунт активен", item.active) : "") +
            (edit && item.max_bound
              ? `<div class="form-section"><p class="muted">Для смены аккаунта MAX отвяжите текущий, затем выпустите новое приглашение.</p>${button("Отвязать MAX", "unbind")}</div>`
              : ""),
          {
            bind: (form) =>
              delegate(form, async (action) => {
                if (action !== "unbind") return;
                if (
                  !(await ctx.confirm(
                    "Отвязать аккаунт MAX?",
                    "Действующие сеансы кабинета будут отозваны. Для подключения потребуется новое приглашение.",
                    "Отвязать",
                  ))
                )
                  return;
                await ctx.api.post(`/api/identities/${id}/unbind`);
                notify("Аккаунт MAX отвязан");
                document.querySelector("#drawer").close();
                await ctx.refresh();
              }),
            submit: async (form) => {
              const fields = formObject(form);
              const body = {
                name: fields.name,
                contact_url: fields.contact_url || null,
                roles: new FormData(form).getAll("roles"),
                branch_codes: ctx.refs.branches
                  ? readScopes(form)
                  : csv(fields.branches),
                manage_access: trusted && !!fields.manage_access,
              };
              let key;
              if (edit) {
                await ctx.api.put(`/api/identities/${id}`, {
                  ...body,
                  active: !!fields.active,
                });
                notify("Права сохранены");
              } else {
                key = (await ctx.api.post("/api/identities", body)).api_token;
              }
              await ctx.refresh();
              return key ? () => showKey(key) : null;
            },
          },
        );
      }),
  };
}

export async function settingsPage(ctx) {
  const config = await ctx.api.get("/api/admin/config");
  const policies = ctx.me.capabilities.manage_hr
    ? await policyPanel(ctx)
    : { html: "", handle: () => false };
  let operations = null;
  if (ctx.me.capabilities.manage_access)
    operations = await ctx.api.get("/api/operations");
  return {
    html:
      heading(
        "Настройки пространства",
        "Подключение MAX, диагностика и демонстрационный календарь.",
      ) +
      `<div class="grid-2"><div class="card"><h2>Мини-приложение MAX</h2><p class="muted">${config.max_configured ? "Публичный адрес настроен. Администраторы получают кнопку кабинета в боте." : "Пока работает локальный кабинет. Для открытия внутри MAX нужен публичный HTTPS-адрес и регистрация URL у владельца бота."}</p><div class="notice">${icon("lock")}Подпись MAX и административные права проверяются на сервере. Аккаунт сотрудника не получает доступ по одной ссылке.</div></div><div class="card"><h2>Область управления</h2><p>${e(ctx.me.branch_codes.includes("*") ? "Все площадки компании" : ctx.me.branch_codes.join(", "))}</p><p class="muted">Назначенные роли: ${e(ctx.me.roles.map(roleName).join(", "))}.</p></div></div>${operations ? `<div class="card form-section"><h2>Фоновые процессы</h2><div class="grid-2"><div><span class="field-label">Задания</span><div class="code">${e(JSON.stringify(operations.jobs, null, 2))}</div></div><div><span class="field-label">Доставка</span><div class="code">${e(JSON.stringify(operations.outbox, null, 2))}</div></div></div><p class="muted">Последний сигнал worker: ${dateTime(operations.worker_heartbeat?.at)}</p></div>` : ""}${ctx.me.capabilities.demo ? `<div class="card form-section"><h2>Демонстрационный календарь</h2><p class="muted">Меняет время только этого тестового стенда. Не используйте на рабочей базе.</p><div class="field-grid">${input("test-time", "Время с часовым поясом", "", "text", 'placeholder="2026-11-06T10:00:00+03:00"')}<div class="actions">${button("Установить время", "clock")}${button("Вернуть реальное", "clock-reset")}${button("Обработать очередь", "tick")}</div></div></div>` : ""}` +
      (ctx.me.capabilities.manage_hr
        ? `<div class="card"><h2>Подстановки в сообщениях</h2><div class="detail-list">${ctx.refs.reference.placeholders.map((p) => `<div><code>${e(p.key)}</code><span>${e(p.description)}</span></div>`).join("")}</div><p class="muted">Значения берутся из анкеты сотрудника, площадки и назначенных HR/руководителя. Менять их можно в соответствующих карточках. Набор ключей фиксирует backend.</p>${ctx.me.branch_codes.includes("*") ? button("Установить исходный маршрут v0.4", "install-v04") : ""}</div>`
        : "") +
      policies.html,
    bind: (container) =>
      delegate(container, async (action) => {
        if (policies.handle(action)) return;
        if (action === "install-v04") {
          await ctx.api.post("/api/admin/route-v04/install");
          notify(
            "Маршрут и реестр установлены. Назначьте сценарий площадке и загрузите материалы",
          );
          await ctx.refresh();
          return;
        }
        if (action === "tick") {
          await ctx.api.post("/api/demo/tick");
          notify("Очередь обработана");
        } else {
          const value =
            action === "clock-reset"
              ? null
              : container.querySelector('[name="test-time"]').value;
          if (action === "clock" && !value)
            throw new Error("Введите время с часовым поясом");
          await ctx.api.put("/api/demo/clock", { now: value });
          notify("Время обновлено");
        }
      }),
  };
}
