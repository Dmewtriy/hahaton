import {
  e,
  icon,
  heading,
  button,
  iconButton,
  badge,
  empty,
  dateTime,
  input,
  select,
  textarea,
  checkbox,
  formObject,
  drawer,
  notify,
  confirmAction,
  toolbar,
  bindFilters,
  table,
  bindPagination,
  delegate,
} from "./components.js";

const audienceLabel = (audience) =>
  ({
    all: "Все доступные сотрудники",
    branches: "Выбранные площадки",
    departments: "Выбранные подразделения",
    employees: "Выбранные сотрудники",
  })[audience.mode] || "Получатели";

export async function campaignsPage(ctx) {
  const state = (ctx.states.campaigns ||= {
    search: "",
    archived: false,
    page: 0,
  });
  const data = await ctx.api.get(
    `/api/admin/campaigns?archived=${state.archived}`,
  );
  let container;
  const render = () => {
    const rows = data.filter((c) =>
      c.title.toLowerCase().includes(state.search.toLowerCase()),
    );
    const region = container.querySelector("[data-list]");
    region.innerHTML = table(
      [
        [
          "Рассылка",
          (c) =>
            `<button class="link-button" data-action="detail" data-id="${e(c.id)}"><strong>${e(c.title)}</strong></button><span class="cell-sub">${c.kind === "survey" ? "Опрос" : "Сообщение"} · ${e(audienceLabel(c.audience))}</span>`,
        ],
        ["Отправка", (c) => dateTime(c.due_at)],
        ["Получатели", (c) => c.recipient_count || "После публикации"],
        ["Статус", (c) => badge(c.status)],
        [
          "",
          (c) =>
            `<div class="actions">${state.archived ? iconButton("restore", "Восстановить рассылку", "restore", c.id) : c.stored_status === "draft" ? button("Запустить", "publish", c.id, "primary") + iconButton("edit", "Редактировать черновик", "edit", c.id) + iconButton("trash", "Удалить черновик", "archive", c.id) : iconButton("copy", "Повторить с новыми получателями", "clone", c.id) + (c.status !== "completed" && c.status !== "cancelled" ? iconButton("close", "Отменить рассылку", "cancel", c.id) : iconButton("trash", "В архив", "archive", c.id))}</div>`,
        ],
      ],
      rows,
      state,
      empty(
        "Рассылок пока нет",
        "Отправьте сообщение или опрос всей команде, отделу или конкретным сотрудникам.",
        state.archived ? "" : "create",
        "Создать рассылку",
      ),
    );
    bindPagination(region, state, render);
  };
  return {
    html:
      heading(
        "Рассылки",
        "Адресные сообщения и опросы с предпросмотром получателей и контролем выполнения.",
        button("Создать рассылку", "create", "", "primary") +
          button("Обновить", "refresh"),
      ) +
      toolbar("Найти рассылку", [], state) +
      "<div data-list></div>",
    bind: (target) => {
      container = target;
      render();
      bindFilters(target, state, render, () => ctx.navigate("campaigns"));
      delegate(target, async (action, id) => {
        const item = data.find((c) => c.id === id);
        if (action === "create") return campaignEditor(ctx);
        if (action === "refresh") return ctx.navigate("campaigns");
        if (action === "edit") return campaignEditor(ctx, item);
        if (action === "clone")
          return campaignEditor(ctx, {
            ...structuredClone(item),
            id: undefined,
            title: item.title + " (копия)",
            revision: null,
            due_at: null,
            expires_at: null,
          });
        if (action === "detail") return campaignDetail(ctx, id);
        if (action === "publish") return publishCampaign(ctx, item);
        if (
          action === "cancel" &&
          !(await confirmAction(
            "Отменить рассылку?",
            "Неотправленные сообщения будут остановлены. Активные опросы завершатся без удаления ответов.",
            "Отменить рассылку",
            true,
          ))
        )
          return;
        if (
          action === "archive" &&
          !(await confirmAction(
            "Переместить рассылку в архив?",
            "Отправки будут остановлены. История и результаты сохранятся.",
            "В архив",
            true,
          ))
        )
          return;
        if (action === "cancel")
          await ctx.api.post(`/api/admin/campaigns/${id}/cancel`);
        if (action === "archive")
          await ctx.api.delete(`/api/admin/campaigns/${id}`);
        if (action === "restore")
          await ctx.api.post(`/api/admin/campaigns/${id}/restore`);
        notify("Рассылка обновлена");
        await ctx.refresh();
      });
    },
  };
}

export async function publishCampaign(ctx, item) {
  const preview = await ctx.api.post(
    "/api/admin/campaigns/preview",
    item.audience,
  );
  if (!preview.count)
    throw new Error("Нет доступных получателей. Измените аудиторию черновика");
  const when =
    new Date(item.due_at) > new Date()
      ? `Отправка запланирована на ${dateTime(item.due_at)}.`
      : "Отправка начнётся после подтверждения.";
  const confirmed = await confirmAction(
    `Отправить ${item.kind === "survey" ? "опрос" : "сообщение"} (получателей: ${preview.count})?`,
    `${when} Подключены к MAX: ${preview.connected}. Остальные получат отправку после подключения.`,
    "Подтвердить отправку",
  );
  if (!confirmed) return;
  await ctx.api.post(`/api/admin/campaigns/${item.id}/publish`, {
    audience_hash: preview.audience_hash,
    revision: item.revision,
  });
  notify("Рассылка опубликована");
  await ctx.refresh();
}

export async function campaignEditor(ctx, item = {}) {
  if (!ctx.refs.employees.length) {
    notify("Сначала добавьте сотрудников", true);
    return ctx.navigate("employees");
  }
  const audience = structuredClone(
    item.audience || {
      mode: "all",
      branch_codes: [],
      department_ids: [],
      employee_ids: [],
      categories: [],
    },
  );
  const due = item.due_at
    ? new Date(
        new Date(item.due_at).getTime() -
          new Date(item.due_at).getTimezoneOffset() * 60000,
      )
        .toISOString()
        .slice(0, 16)
    : "";
  drawer(
    item.id ? "Редактировать черновик" : "Новая рассылка",
    input(
      "title",
      "Название для HR",
      item.title || "",
      "text",
      'required placeholder="Как дела в отделе продаж?"',
    ) +
      select(
        "kind",
        "Что отправляем",
        [
          { value: "message", label: "Сообщение" },
          { value: "survey", label: "Опросник" },
        ],
        item.kind || "message",
        null,
      ) +
      `<div data-template-field>${select(
        "template_id",
        "Опросник",
        ctx.refs.surveys.map((t) => ({
          value: t.id,
          label: `${t.title} · вопросов: ${t.question_count}`,
        })),
        item.template_id || "",
      )}</div>` +
      select("message_type", "Вид сообщения", [{value:"message",label:"Обычное сообщение"},{value:"welcome_letter",label:"Приветственное письмо"},{value:"first_day_card",label:"Открытка первого дня — нужно изображение"}],item.message_type||"message",null) +
      textarea(
        "text",
        "Сообщение сотруднику",
        item.text || "Здравствуйте! Просим поделиться обратной связью.",
        "required",
      ) +
      `<p class="draft-note">Можно использовать {name} — имя, {branch} — площадка, {department} — подразделение.</p><div class="form-section"><h3>Получатели</h3>${select(
        "audience_mode",
        "Кому отправить",
        [
          { value: "all", label: "Всем сотрудникам моей области" },
          { value: "branches", label: "Сотрудникам выбранных площадок" },
          {
            value: "departments",
            label: "Сотрудникам выбранных подразделений",
          },
          { value: "employees", label: "Конкретным сотрудникам" },
        ],
        audience.mode,
        null,
      )}<div data-audience-options></div><div data-audience-summary class="audience-preview">Нажмите «Проверить получателей» перед публикацией.</div><div class="spacer"></div>${button("Проверить получателей", "audience-preview")}</div><div class="form-section"><h3>Когда отправить</h3>${input("due_at", "Дата и время в вашем часовом поясе", due, "datetime-local", "", "Оставьте пустым, чтобы отправить сразу после публикации.")}${input("expires_at", "Пройти / доставить до (необязательно)", item.expires_at ? new Date(new Date(item.expires_at).getTime() - new Date(item.expires_at).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : "", "datetime-local", "", "После срока опрос закроется, ответы сохранятся, напоминания прекратятся.")}<span class="field-label">Вложения</span><div class="recipient-grid">${ctx.refs.documents.filter(d => d.purpose !== "personal_plan").map((d) => checkbox("document_ids", d.title, item.document_ids?.includes(d.id), d.id)).join("") || "<small>Документов пока нет</small>"}</div></div><div data-card-preview></div><div class="notice">${icon("info")}Сейчас создаётся только черновик. Перед отправкой вы отдельно подтвердите получателей. Уже начатые диалоги и опросы будут учтены очередью.</div>`,
    {
      submitLabel: "Сохранить черновик",
      bind: (form) => {
        let imageUrls=[];
        const renderImages=async()=>{
          imageUrls.forEach(u=>URL.revokeObjectURL(u)); imageUrls=[];
          const target=form.querySelector("[data-card-preview]");
          if(!target)return;
          const chosen=new FormData(form).getAll("document_ids");
          const images=ctx.refs.documents.filter(d=>chosen.includes(d.id)&&d.purpose==="card_image");
          target.replaceChildren();
          for(const image of images) {
            try { const objectUrl=await ctx.api.previewImage(image.id);imageUrls.push(objectUrl);
              const element=document.createElement("img");element.src=objectUrl;element.alt=image.title;element.style.maxWidth="100%";element.style.maxHeight="260px";target.append(element);
            } catch(error) {target.textContent=error.message;}
          }
        };
        form.addEventListener("change",event=>{if(event.target.name==="document_ids")renderImages();});
        document.querySelector("#drawer").addEventListener("close",()=>imageUrls.forEach(u=>URL.revokeObjectURL(u)),{once:true});
        renderImages();
        const renderAudience = () => {
          const mode = form.elements.audience_mode.value;
          audience.mode = mode;
          const list =
            mode === "branches"
              ? ctx.refs.branches.map((b) => ({ id: b.code, label: b.name }))
              : mode === "departments"
                ? ctx.refs.departments.map((d) => ({
                    id: d.id,
                    label: `${d.name} · ${d.branch_name}`,
                  }))
                : mode === "employees"
                  ? ctx.refs.employees.map((e) => ({
                      id: e.id,
                      label: `${e.name} · ${e.department}`,
                    }))
                  : [];
          const field = {
            branches: "branch_codes",
            departments: "department_ids",
            employees: "employee_ids",
          }[mode];
          form.querySelector("[data-audience-options]").innerHTML = list.length
            ? `<div class="recipient-grid">${list.map((r) => checkbox("audience_selected", r.label, audience[field]?.includes(r.id), r.id)).join("")}</div>`
            : `<p class="muted">${mode === "all" ? "Все действующие сотрудники в вашей области доступа." : "Для этой группы пока нет получателей."}</p>`;
          form.querySelector("[data-template-field]").hidden =
            form.elements.kind.value !== "survey";
        };
        form.elements.audience_mode.onchange = renderAudience;
        form.elements.kind.onchange = renderAudience;
        form.addEventListener("change", (event) => {
          if (event.target.name === "audience_selected") {
            const field = {
              branches: "branch_codes",
              departments: "department_ids",
              employees: "employee_ids",
            }[audience.mode];
            audience[field] = new FormData(form).getAll("audience_selected");
          }
          form.querySelector("[data-audience-summary]").textContent =
            "Аудитория изменена. Проверьте получателей перед публикацией.";
        });
        delegate(form, async (action) => {
          if (action !== "audience-preview") return;
          const result = await ctx.api.post(
            "/api/admin/campaigns/preview",
            audience,
          );
          form.querySelector("[data-audience-summary]").innerHTML =
            `<strong>Получателей: ${result.count} · в MAX: ${result.connected}</strong><div class="spacer"></div>${result.employees
              .slice(0, 8)
              .map((person) => `<span class="tag">${e(person.name)}</span>`)
              .join(
                " ",
              )}${result.count > 8 ? `<p>И ещё ${result.count - 8}</p>` : ""}`;
        });
        renderAudience();
      },
      submit: async (form) => {
        const fields = formObject(form),
          body = {
            title: fields.title,
            kind: fields.kind,
            message_type: fields.message_type || "message",
            text: fields.text,
            template_id:
              fields.kind === "survey" ? fields.template_id || null : null,
            audience,
            expires_at: fields.expires_at
              ? new Date(fields.expires_at).toISOString()
              : null,
            due_at: fields.due_at
              ? new Date(fields.due_at).toISOString()
              : null,
            document_ids: new FormData(form).getAll("document_ids"),
            revision: item.revision || null,
          };
        if (body.kind === "survey" && !body.template_id)
          throw new Error("Выберите опросник");
        if (item.id) await ctx.api.put(`/api/admin/campaigns/${item.id}`, body);
        else await ctx.api.post("/api/admin/campaigns", body);
        notify("Черновик сохранён. Рассылку можно проверить и запустить");
        await ctx.refresh();
      },
    },
  );
}

export async function campaignDetail(ctx, id) {
  const data = await ctx.api.get(`/api/admin/campaigns/${id}`);
  const completed = data.progress.completed || 0,
    sent = data.progress.sent || 0;
  const questions = data.survey_snapshot?.questions || [];
  const aggregate = questions
    .map((q) => {
      const values = data.answers.filter((a) => a.question_id === q.id);
      const stats = data.question_statistics.find(
        (s) => s.question_id === q.id,
      );
      let summary = `${stats?.answer_count || 0} ответов`;
      if (stats?.average != null)
        summary += ` · средняя оценка ${stats.average}`;
      return `<div class="form-section"><h3>${e(q.text)}</h3><p class="muted">${summary}</p>${values
        .map((a) => {
          const v = a.value.answer;
          const text = q.options?.length
            ? (Array.isArray(v) ? v : [v])
                .map((x) => q.options.find((o) => o.id === x)?.label || x)
                .join(", ")
            : v;
          return `<div class="tick-row"><div><strong>${e(a.employee_name)}</strong><p>${e(text)}</p>${a.comment ? `<p>Комментарий: ${e(a.comment)}</p>` : ""}</div></div>`;
        })
        .join("")}</div>`;
    })
    .join("");
  drawer(
    data.title,
    `<div class="actions">${badge(data.status)}${data.stored_status === "draft" ? button("Редактировать", "edit") + button("Запустить", "publish", "", "primary") : ""}</div><div class="spacer"></div><div class="result-summary"><div class="card"><strong>${data.recipient_count}</strong><small>Получателей</small></div><div class="card"><strong>${completed}</strong><small>Завершили опрос</small></div><div class="card"><strong>${sent}</strong><small>Сообщений отправлено</small></div></div><div class="form-section"><h3>Сообщение</h3><div class="bubble">${e(data.text)}</div><p class="muted">${e(audienceLabel(data.audience))} · ${dateTime(data.due_at)}</p></div><div class="form-section"><h3>Получатели и выполнение</h3>${data.recipients.length ? data.recipients.map((r) => `<div class="card-row"><div><strong>${e(r.name)}</strong><p>${e(r.department)}</p></div>${badge(r.status)}${r.result ? `<span>${r.result.points}/${r.result.maximum} · ${r.result.percent}% · ${r.result.passed ? "пройден" : "не пройден"}</span>` : ""}</div>`).join("") : '<p class="muted">Получатели зафиксируются при публикации.</p>'}</div>${aggregate}`,
    {
      bind: (form) =>
        delegate(form, async (action) => {
          if (action === "edit") return campaignEditor(ctx, data);
          if (action === "publish") {
            await publishCampaign(ctx, data);
            document.querySelector("#drawer").close();
          }
        }),
    },
  );
}
