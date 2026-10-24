import {
  e,
  icon,
  heading,
  button,
  iconButton,
  badge,
  empty,
  initials,
  date,
  dateTime,
  input,
  select,
  textarea,
  checkbox,
  formObject,
  csv,
  drawer,
  notify,
  confirmAction,
  copyText,
  toolbar,
  bindFilters,
  table,
  bindPagination,
  delegate,
} from "./components.js";

const named = (items, key = "id") =>
  items.map((x) => ({ value: x[key], label: x.name }));
const personCell = (row) =>
  `<div class="person"><span class="avatar">${e(initials(row.name))}</span><div><button class="link-button" data-action="detail" data-id="${e(row.id)}"><strong>${e(row.name)}</strong></button><small>${e(row.category === "office" ? "Офисный сотрудник" : row.category === "production" ? "Производство" : row.category)}</small></div></div>`;

export async function employeesPage(ctx) {
  const state = (ctx.states.employees ||= {
    search: "",
    branch: "",
    archived: false,
    page: 0,
  });
  const data = await ctx.api.get(
    `/api/admin/employees?archived=${state.archived}`,
  );
  let container;
  const filtered = () =>
    data.filter(
      (r) =>
        (!state.branch || r.branch_code === state.branch) &&
        (!state.search ||
          `${r.name} ${r.department} ${r.recruiter_name}`
            .toLowerCase()
            .includes(state.search.toLowerCase())),
    );
  const columns = [
    ["Сотрудник", personCell],
    [
      "Подразделение",
      (r) =>
        `${e(r.department)}<span class="cell-sub">${e(r.branch_name)}</span>`,
    ],
    ["Выход на работу", (r) => date(r.hire_date)],
    ["Рекрутер", (r) => e(r.recruiter_name)],
    [
      "Подключение",
      (r) => (state.archived ? badge("stopped") : badge(r.status)),
    ],
    [
      "",
      (r) =>
        `<div class="actions">${state.archived ? iconButton("restore", "Восстановить сотрудника", "restore", r.id) : iconButton("edit", "Редактировать сотрудника", "edit", r.id) + iconButton("send", "Приглашение в MAX", "invite", r.id) + iconButton("trash", "В архив", "archive", r.id)}</div>`,
    ],
  ];
  const render = () => {
    const region = container.querySelector("[data-list]");
    region.innerHTML = table(
      columns,
      filtered(),
      state,
      empty(
        state.archived ? "Архив пуст" : "Сотрудники не найдены",
        "Добавьте нового сотрудника или измените условия поиска.",
        state.archived ? "" : "create",
        "Добавить сотрудника",
      ),
    );
    bindPagination(region, state, render);
  };
  return {
    html:
      heading(
        "Сотрудники",
        "Анкеты, приглашения и прогресс адаптации вашей команды.",
        button("Добавить сотрудника", "create", "", "primary"),
      ) +
      toolbar("Имя, подразделение или рекрутер", ctx.refs.branches, state) +
      "<div data-list></div>",
    bind: (target) => {
      container = target;
      render();
      bindFilters(target, state, render, () => ctx.navigate("employees"));
      delegate(target, async (action, id) => {
        if (action === "create" || action === "edit")
          return employeeEditor(ctx, id);
        if (action === "detail") return employeeDetail(ctx, id);
        if (action === "invite") return invitation(ctx, id);
        if (
          action === "archive" &&
          !(await confirmAction(
            "Архивировать сотрудника?",
            "Доступ и будущие отправки будут остановлены. Ответы и история сохранятся.",
            "В архив",
            true,
          ))
        )
          return;
        if (action === "archive")
          await ctx.api.delete(`/api/admin/employees/${id}`);
        if (action === "restore")
          await ctx.api.post(`/api/admin/employees/${id}/restore`);
        notify(
          action === "restore"
            ? "Сотрудник восстановлен"
            : "Сотрудник в архиве",
        );
        await ctx.refresh();
      });
    },
  };
}

export async function invitation(ctx, id) {
  const data = await ctx.api.post(`/api/admin/employees/${id}/invitation`);
  const text = data.url || data.command;
  drawer(
    "Приглашение в MAX",
    `<div class="notice">${icon("send")}Приглашение одноразовое и действует ${data.hours} ч. Новое приглашение заменяет предыдущее.</div><div class="code">${e(text)}</div><div class="spacer"></div>${button("Скопировать приглашение", "copy-invite", "", "primary")}<p class="draft-note">Отправьте его сотруднику. После открытия ссылки аккаунт MAX свяжется с анкетой.</p>`,
    { bind: (form) => delegate(form, () => copyText(text)) },
  );
}

export async function employeeEditor(ctx, id) {
  const item = id ? await ctx.api.get(`/api/admin/employees/${id}`) : {};
  const branch = item.branch_code || ctx.refs.branches[0]?.code || "";
  if (!ctx.refs.branches.length) {
    notify("Сначала создайте площадку", true);
    return ctx.navigate("branches");
  }
  const localToday = new Date(
    Date.now() - new Date().getTimezoneOffset() * 60000,
  )
    .toISOString()
    .slice(0, 10);
  const body = `<div class="field-grid">${input("name", "Имя и фамилия", item.name || "", "text", 'required autocomplete="name"')}${select("branch_code", "Площадка", named(ctx.refs.branches, "code"), branch, null)}${select("department", "Подразделение", [], "", "Выберите подразделение")}${select("category", "Категория сотрудника", [{ value: "office", label: "Офисный сотрудник" }, { value: "production", label: "Производство" }, ...(!["office", "production", undefined].includes(item.category) ? [{ value: item.category, label: item.category }] : [])], item.category || "office", null)}${select("recruiter_id", "Закреплённый рекрутер", [], "", "Выберите HR")}${select("manager_id", "Руководитель", [], "", "Не назначен")}${input("hire_date", "Дата выхода", item.hire_date || localToday, "date", "required")}${input("probation_end", "Окончание адаптации", item.probation_end || "", "date", "", "Пусто — три календарных месяца после выхода.")}${select(
    "timezone",
    "Часовой пояс сотрудника",
    ctx.refs.reference.timezones.map((t) => ({ value: t, label: t })),
    item.timezone ||
      ctx.refs.branches.find((b) => b.code === branch)?.timezone ||
      "Europe/Moscow",
    null,
  )}${input("adaptation_plan_url", "Персональный адаптационный план", item.adaptation_plan_url || "", "url")}${input("it_access_url", "Подготовленные ИТ-доступы", item.it_access_url || "", "url")}</div><p class="draft-note">Подразделения создаются в разделе «Подразделения». Ссылки на персональные материалы предоставляются ответственным HR.</p><div class="notice">${icon("info")}После изменения даты выхода или площадки будут пересчитаны только будущие шаги адаптации. Начатые опросы сохранятся.</div>`;
  drawer(id ? "Редактировать сотрудника" : "Новый сотрудник", body, {
    subtitle: id ? item.name : "Пригласите человека в адаптацию",
    submitLabel: id ? "Сохранить изменения" : "Создать сотрудника",
    bind: (form) => {
      const fill = (initial = false) => {
        const code = form.elements.branch_code.value;
        const available = ctx.refs.staff.filter(
          (s) => s.branch_codes.includes("*") || s.branch_codes.includes(code),
        );
        const replace = (name, role, selected) => {
          const list = available.filter((s) =>
            s.roles.some((r) => role.includes(r)),
          );
          form.elements[name].innerHTML =
            `<option value="">${name === "manager_id" ? "По правилу HR или подразделения" : "Выберите рекрутера"}</option>${name === "manager_id" ? '<option value="__none__">Оставить без руководителя</option>' : ""}` +
            list
              .map(
                (s) =>
                  `<option value="${e(s.id)}" ${s.id === selected ? "selected" : ""}>${e(s.name)}</option>`,
              )
              .join("");
        };
        replace(
          "recruiter_id",
          ["hr", "hr_head"],
          initial ? item.recruiter_id || ctx.me.id : ctx.me.id,
        );
        replace(
          "manager_id",
          ["manager"],
          initial
            ? id && !item.manager_id
              ? "__none__"
              : item.manager_id
            : "",
        );
        if (initial && id && !item.manager_id)
          form.elements.manager_id.value = "__none__";
        form.elements.department.innerHTML =
          '<option value="">Выберите подразделение</option>' +
          ctx.refs.departments
            .filter((d) => d.branch_code === code)
            .map(
              (d) =>
                `<option value="${e(d.name)}" ${initial && d.name === item.department ? "selected" : ""}>${e(d.name)}</option>`,
            )
            .join("");
        if (
          initial &&
          item.department &&
          ![...form.elements.department.options].some(
            (o) => o.value === item.department,
          )
        ) {
          const option = document.createElement("option");
          option.value = item.department;
          option.textContent = item.department + " (текущее)";
          option.selected = true;
          form.elements.department.append(option);
        }
        form.elements.department.required = true;
        if (!initial) {
          form.elements.department.value = "";
          form.elements.timezone.value =
            ctx.refs.branches.find((b) => b.code === code)?.timezone ||
            "Europe/Moscow";
        }
      };
      fill(true);
      form.elements.branch_code.onchange = () => fill();
    },
    submit: async (form) => {
      const data = formObject(form),
        department = ctx.refs.departments.find(
          (d) =>
            d.branch_code === data.branch_code && d.name === data.department,
        );
      const body = {
        ...data,
        manager_id:
          data.manager_id === "__none__" ? null : data.manager_id || null,
        auto_assign_manager: data.manager_id !== "__none__",
        probation_end: data.probation_end || null,
        adaptation_plan_url: data.adaptation_plan_url || null,
        it_access_url: data.it_access_url || null,
        department_id: department?.id || null,
        revision: item.revision || null,
        active: item.active ?? true,
      };
      if (!body.recruiter_id) throw new Error("Назначьте рекрутера");
      if (id) await ctx.api.put(`/api/admin/employees/${id}`, body);
      else await ctx.api.post("/api/admin/employees", body);
      notify(
        id
          ? "Анкета обновлена"
          : "Сотрудник создан. Теперь можно выдать приглашение",
      );
      await ctx.refresh();
    },
  });
}

export async function employeeDetail(ctx, id) {
  const item = await ctx.api.get(`/api/admin/employees/${id}`);
  const fields = [
    ["Площадка", item.branch_name],
    ["Подразделение", item.department],
    ["Рекрутер", item.recruiter_name],
    ["Руководитель", item.manager_name],
    ["Выход", date(item.hire_date)],
    ["Окончание адаптации", date(item.probation_end)],
    [
      "Состояние адаптации",
      {
        active: "Адаптация",
        preboarding: "До выхода",
        completed: "Завершена",
        stopped: "Остановлена",
        invited: "Ожидает подключения",
      }[item.onboarding_phase] || item.onboarding_phase,
    ],
  ];
  const schedule = item.schedule.filter((s) => s.status !== "cancelled");
  const answersHtml = item.surveys
    .map(
      (run) =>
        `<div class="form-section"><div class="card-header"><h3>${e(run.definition.title)}</h3>${badge(run.status)}</div>${run.result ? `<p>Результат теста: ${run.result.points} / ${run.result.maximum} (${run.result.percent}%). ${run.result.passed ? "Пройден" : "Порог не достигнут"}.</p>` : ""}${run.expires_at ? `<p>Срок: ${dateTime(run.expires_at)}</p>` : ""}${
          run.answers.length
            ? run.answers
                .map((a) => {
                  const q = run.definition.questions.find(
                      (q) => q.id === a.question_id,
                    ),
                    value = a.value.answer;
                  const answer = q?.options?.length
                    ? (Array.isArray(value) ? value : [value])
                        .map(
                          (v) => q.options.find((o) => o.id === v)?.label || v,
                        )
                        .join(", ")
                    : value;
                  return `<div class="tick-row"><div><strong>${e(q?.text || a.question_id)}</strong><p>${e(answer)}</p>${a.comment ? `<p>Комментарий: ${e(a.comment)}</p>` : ""}${a.analysis ? `<span class="badge purple">${e(a.analysis.processor_version)}</span><p>${e(a.analysis.explanation)}</p>` : ""}</div></div>`;
                })
                .join("")
            : '<p class="muted">Ответов пока нет</p>'
        }</div>`,
    )
    .join("");
  drawer(
    item.name,
    `<div class="actions">${badge(item.status)}${button("Редактировать", "edit", id)}${item.onboarding_status === "active" ? button("Завершить адаптацию", "onboarding-complete", id) + button("Остановить адаптацию", "onboarding-stop", id) : button("Возобновить адаптацию", "onboarding-resume", id)}${item.active ? button("Пригласить в MAX", "invite", id) : ""}</div><div class="spacer"></div><div class="detail-list">${fields.map(([key, value]) => `<div><span>${key}</span><strong>${e(value)}</strong></div>`).join("")}</div><div class="detail-tab"><button type="button" data-tab="timeline" class="active">План адаптации</button><button type="button" data-tab="answers">Ответы на опросы</button></div><div data-panel="timeline">${schedule.length ? schedule.map((s) => `<div class="tick-row"><span class="tick-dot"></span><div style="flex:1"><strong>${e(s.text.slice(0, 120))}</strong><p>${dateTime(s.due_at)} · ${s.campaign_id ? "Рассылка HR" : "Адаптация"}</p></div><div>${badge(s.status)}${["manual", "late_review"].includes(s.status) ? button("Запустить", "trigger", s.id) : ""}</div></div>`).join("") : empty("План пока не назначен", "Выберите сценарий для площадки сотрудника.")}</div><div data-panel="answers" hidden>${answersHtml || empty("Опросов пока нет", "Здесь появятся ответы сотрудника и прогресс прохождения.")}</div>`,
    {
      bind: (form) => {
        form.querySelectorAll("[data-tab]").forEach(
          (b) =>
            (b.onclick = () => {
              form
                .querySelectorAll("[data-tab]")
                .forEach((x) => x.classList.toggle("active", x === b));
              form
                .querySelectorAll("[data-panel]")
                .forEach((p) => (p.hidden = p.dataset.panel !== b.dataset.tab));
            }),
        );
        delegate(form, async (action, key) => {
          if (action === "edit") return employeeEditor(ctx, id);
          if (action === "invite") return invitation(ctx, id);
          if (action.startsWith("onboarding-")) {
            if (
              !(await confirmAction(
                "Изменить состояние адаптации?",
                "Общий доступ, переписка HR и общие рассылки сохраняются. Начатые ответы остаются в истории.",
                "Подтвердить",
              ))
            )
              return;
            await ctx.api.post(`/api/admin/employees/${id}/onboarding`, {
              action: action.slice(11),
            });
            notify("Состояние адаптации изменено");
            await employeeDetail(ctx, id);
            return;
          }
          if (action === "trigger") {
            await ctx.api.post(`/api/steps/${key}/trigger`);
            notify("Шаг поставлен в очередь");
            await employeeDetail(ctx, id);
          }
        });
      },
    },
  );
}

const resourceNames = {
  food: "Питание",
  map: "Карта",
  transport: "Транспорт",
  portal: "Корпоративный портал",
  employment_documents: "Документы для оформления",
  company: "О компании",
  contacts: "Справочник контактов",
  adaptation_plan: "Адаптационный план",
  sports: "Спортивный комплекс",
  events: "Мероприятия",
  movies: "Фильмы",
  books: "Книги",
  playlist: "Плейлист",
  welcome_letter: "Приветственное письмо",
};

export async function branchesPage(ctx) {
  const state = (ctx.states.branches ||= {
    archived: false,
    search: "",
    page: 0,
  });
  const rows = await ctx.api.get(
    `/api/admin/branches?archived=${state.archived}`,
  );
  const cards = rows
    .map(
      (b) =>
        `<article class="card resource-card"><div class="card-header"><div class="resource-icon">${icon("branch")}</div><span class="badge">${b.employee_count} сотрудников</span></div><h3>${e(b.name)}</h3><p>${e(b.timezone)} · ${e(b.code)}</p><p>${e(b.scenario_name || "Сценарий пока не назначен")}</p><footer><span class="muted">${Object.values(b.resources).filter(Boolean).length} материалов</span><div class="actions">${state.archived ? iconButton("restore", "Восстановить площадку", "restore", b.code) : iconButton("edit", "Настроить площадку", "edit", b.code) + iconButton("trash", "В архив", "archive", b.code)}</div></footer></article>`,
    )
    .join("");
  return {
    html:
      heading(
        "Площадки",
        "Календари, материалы и сценарии филиалов без изменений кода.",
        button("Добавить площадку", "create", "", "primary"),
      ) +
      toolbar("Поиск площадки", [], state) +
      `<div class="grid-3" data-cards>${cards || empty("Площадок пока нет", "Создайте площадку, чтобы приглашать сотрудников.", "create", "Добавить площадку")}</div>`,
    bind: (target) => {
      bindFilters(
        target,
        state,
        () =>
          target
            .querySelectorAll("[data-cards] article")
            .forEach(
              (card, i) =>
                (card.hidden = !rows[i].name
                  .toLowerCase()
                  .includes(state.search.toLowerCase())),
            ),
        () => ctx.navigate("branches"),
      );
      delegate(target, async (action, id) => {
        if (["create", "edit"].includes(action))
          return branchEditor(
            ctx,
            rows.find((b) => b.code === id),
          );
        if (
          action === "archive" &&
          !(await confirmAction(
            "Архивировать площадку?",
            "Площадку с активными сотрудниками сначала нужно освободить. Материалы и история сохранятся.",
            "В архив",
            true,
          ))
        )
          return;
        if (action === "archive")
          await ctx.api.delete(`/api/admin/branches/${id}`);
        if (action === "restore")
          await ctx.api.post(`/api/admin/branches/${id}/restore`);
        notify("Площадка обновлена");
        await ctx.refresh();
      });
    },
  };
}

function branchEditor(ctx, item = {}) {
  const latest = Object.values(
    ctx.refs.scenarios.reduce((acc, s) => {
      if (!acc[s.name] || s.version > acc[s.name].version) acc[s.name] = s;
      return acc;
    }, {}),
  );
  let resources = Object.entries(item.resources || {}).map(([key, value]) => ({
    key,
    label: item.resource_labels?.[key] || resourceNames[key] || key,
    value,
  }));
  if (!resources.length)
    resources = ["food", "map", "transport", "portal"].map((key) => ({
      key,
      label: resourceNames[key],
      value: "",
    }));
  drawer(
    item.code ? "Настройки площадки" : "Новая площадка",
    `<div class="field-grid">${input("name", "Название", item.name || "", "text", "required")}${input("code", "Короткий код", item.code || "", "text", `required pattern="[A-Za-z0-9_-]{1,64}" ${item.code ? "readonly" : ""}`, "Латинские буквы, цифры, дефис. После создания не меняется.")}${select(
      "timezone",
      "Часовой пояс",
      ctx.refs.reference.timezones.map((t) => ({ value: t, label: t })),
      item.timezone || "Europe/Moscow",
      null,
    )}${select(
      "scenario_id",
      "Сценарий адаптации",
      latest.map((s) => ({
        value: s.id,
        label: `${s.name} · версия ${s.version}`,
      })),
      item.scenario_id || "",
      "Пока не назначен",
    )}</div>${select(
      "route_variant",
      "Создать исходный маршрут v0.4 по образцу",
      [
        { value: "", label: "Оставить выбранный сценарий" },
        { value: "GrPP", label: "ГрПП" },
        { value: "GTP", label: "ГТП" },
        { value: "Luzhniki", label: "Лужники" },
      ],
      "",
      null,
    )}<p class="draft-note">Для новой площадки можно создать собственную конфигурацию по образцу. Все материалы будут настраиваться отдельно; программирование не требуется.</p><div class="form-section"><h3>Рабочий адрес</h3><p class="muted">Используется для команды /places. Домашний адрес сотрудника не запрашивается.</p><div class="field-grid">${input("work_city", "Город", item.workplace?.city || "")}${input("work_street", "Улица", item.workplace?.street || "")}${input("work_building", "Дом / корпус", item.workplace?.building || "")}</div>${select(
      "late_join_policy",
      "Позднее подключение сотрудника",
      [
        {
          value: "review",
          label: "Пропущенные шаги запускает HR вручную",
        },
        {
          value: "catch_up",
          label: "Отправлять пропущенные шаги до конца адаптации",
        },
        {
          value: "skip_past",
          label: "Пропустить прошедшие шаги, оставить будущие",
        },
      ],
      item.late_join_policy || "review",
      null,
    )}</div><div class="form-section"><h3>Другие разрешённые получатели обращений</h3><p class="muted">Помимо закреплённого рекрутера. HR должен иметь доступ к этой площадке.</p>${ctx.refs.staff
      .filter((s) => s.roles.some((r) => ["hr", "hr_head"].includes(r)))
      .map(
        (s) =>
          `<label class="check"><input type="checkbox" data-support-recipient value="${e(s.id)}" ${(item.support_recipient_ids || []).includes(s.id) ? "checked" : ""}>${e(s.name)}</label>`,
      )
      .join(
        "",
      )}</div><div class="form-section"><h3>Рабочий календарь</h3><div class="weekday-row">${["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((name, i) => checkbox("workdays", name, (item.workdays || [0, 1, 2, 3, 4]).includes(i), String(i))).join("")}</div><div class="spacer"></div><div class="field-grid">${input("holidays", "Нерабочие даты", (item.holidays || []).join(", "), "text", 'placeholder="2026-11-04, 2027-01-01"')}${input("working_dates", "Дополнительные рабочие даты", (item.working_dates || []).join(", "), "text", 'placeholder="2026-12-26"')}</div></div><div class="form-section"><div class="card-header"><h3>Материалы площадки</h3>${button("Добавить материал", "add-resource")}</div><div data-resource-editor></div></div>`,
    {
      bind: (form) => {
        const render = () => {
          form.querySelector("[data-resource-editor]").innerHTML = resources
            .map(
              (r, i) =>
                `<div class="question-card" data-resource-index="${i}"><div class="inline-row"><input aria-label="Название материала" data-resource-label value="${e(r.label)}" placeholder="Название">${iconButton("trash", "Удалить материал", "remove-resource", i)}</div><div class="spacer"></div><textarea data-resource-value aria-label="Ссылка или текст материала" placeholder="Ссылка или инструкция">${e(r.value)}</textarea></div>`,
            )
            .join("");
        };
        form.addEventListener("input", (event) => {
          const row = event.target.closest("[data-resource-index]");
          if (row)
            resources[Number(row.dataset.resourceIndex)][
              event.target.hasAttribute("data-resource-label")
                ? "label"
                : "value"
            ] = event.target.value;
        });
        delegate(form, (action, id) => {
          if (action === "add-resource")
            resources.push({
              key: "material_" + crypto.randomUUID().slice(0, 8),
              label: "",
              value: "",
            });
          if (action === "remove-resource") resources.splice(Number(id), 1);
          if (["add-resource", "remove-resource"].includes(action)) render();
        });
        render();
      },
      submit: async (form) => {
        const fields = formObject(form),
          workdays = new FormData(form).getAll("workdays").map(Number);
        if (!workdays.length)
          throw new Error("Выберите хотя бы один рабочий день");
        if (resources.some((r) => r.value && !r.label.trim()))
          throw new Error("Укажите название каждого материала");
        const body = {
          code: fields.code,
          name: fields.name,
          workplace: {
            city: fields.work_city,
            street: fields.work_street,
            building: fields.work_building,
          },
          late_join_policy: fields.late_join_policy,
          support_recipient_ids: [
            ...form.querySelectorAll("[data-support-recipient]:checked"),
          ].map((x) => x.value),
          timezone: fields.timezone,
          scenario_id: fields.scenario_id || null,
          workdays,
          holidays: csv(fields.holidays),
          working_dates: csv(fields.working_dates),
          resources: Object.fromEntries(resources.map((r) => [r.key, r.value])),
          resource_labels: Object.fromEntries(
            resources.map((r) => [r.key, r.label]),
          ),
          revision: item.revision || null,
          active: item.active ?? true,
        };
        const saved = await ctx.api.put(
          `/api/admin/branches/${encodeURIComponent(body.code)}`,
          body,
        );
        if (fields.route_variant)
          await ctx.api.post(
            `/api/admin/branches/${encodeURIComponent(body.code)}/route-v04`,
            { variant: fields.route_variant, revision: saved.revision },
          );
        notify("Площадка сохранена");
        await ctx.refresh();
      },
    },
  );
}

export async function departmentsPage(ctx) {
  const state = (ctx.states.departments ||= {
    search: "",
    branch: "",
    archived: false,
    page: 0,
  });
  const rows = await ctx.api.get(
    `/api/admin/departments?archived=${state.archived}`,
  );
  let container;
  const render = () => {
    const filtered = rows.filter(
      (d) =>
        (!state.branch || d.branch_code === state.branch) &&
        d.name.toLowerCase().includes(state.search.toLowerCase()),
    );
    const region = container.querySelector("[data-list]");
    region.innerHTML = table(
      [
        ["Подразделение", (d) => `<strong>${e(d.name)}</strong>`],
        ["Площадка", (d) => e(d.branch_name)],
        [
          "Руководитель",
          (d) =>
            e(
              ctx.refs.staff.find((s) => s.id === d.manager_id)?.name ||
                "Не назначен",
            ),
        ],
        ["Сотрудников", (d) => d.employee_count],
        [
          "",
          (d) =>
            `<div class="actions">${state.archived ? iconButton("restore", "Восстановить подразделение", "restore", d.id) : iconButton("edit", "Редактировать подразделение", "edit", d.id) + iconButton("trash", "В архив", "archive", d.id)}</div>`,
        ],
      ],
      filtered,
      state,
      empty(
        "Подразделения не найдены",
        "Добавьте подразделение или измените фильтр.",
        "create",
        "Добавить подразделение",
      ),
    );
    bindPagination(region, state, render);
  };
  return {
    html:
      heading(
        "Подразделения",
        "Структура компании для назначения руководителей и адресных опросов.",
        button("Добавить подразделение", "create", "", "primary"),
      ) +
      toolbar("Название подразделения", ctx.refs.branches, state) +
      "<div data-list></div>",
    bind: (target) => {
      container = target;
      render();
      bindFilters(target, state, render, () => ctx.navigate("departments"));
      delegate(target, async (action, id) => {
        if (action === "create" || action === "edit")
          return departmentEditor(
            ctx,
            rows.find((r) => r.id === id),
          );
        if (
          action === "archive" &&
          !(await confirmAction(
            "Архивировать подразделение?",
            "Активных сотрудников нужно предварительно перевести в другое подразделение.",
            "В архив",
            true,
          ))
        )
          return;
        if (action === "archive")
          await ctx.api.delete(`/api/admin/departments/${id}`);
        if (action === "restore")
          await ctx.api.post(`/api/admin/departments/${id}/restore`);
        notify("Подразделение обновлено");
        await ctx.refresh();
      });
    },
  };
}

function departmentEditor(ctx, item = {}) {
  drawer(
    item.id ? "Редактировать подразделение" : "Новое подразделение",
    input("name", "Название", item.name || "", "text", "required") +
      select(
        "branch_code",
        "Площадка",
        named(ctx.refs.branches, "code"),
        item.branch_code || ctx.refs.branches[0]?.code,
        null,
      ) +
      select(
        "manager_id",
        "Руководитель",
        named(ctx.refs.staff.filter((s) => s.roles.includes("manager"))),
        item.manager_id || "",
        "Не назначен",
      ),
    {
      submit: async (form) => {
        const data = formObject(form);
        data.manager_id ||= null;
        data.revision = item.revision || null;
        if (item.id)
          await ctx.api.put(`/api/admin/departments/${item.id}`, data);
        else await ctx.api.post("/api/admin/departments", data);
        notify("Подразделение сохранено");
        await ctx.refresh();
      },
    },
  );
}
