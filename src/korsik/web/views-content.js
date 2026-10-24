import {
  e,
  icon,
  heading,
  button,
  iconButton,
  badge,
  empty,
  date,
  input,
  select,
  textarea,
  checkbox,
  formObject,
  csv,
  scopes,
  readScopes,
  drawer,
  notify,
  confirmAction,
  toolbar,
  bindFilters,
  table,
  bindPagination,
  delegate,
} from "./components.js";

export async function documentsPage(ctx) {
  const state = (ctx.states.documents ||= {
    search: "",
    branch: "",
    archived: false,
    page: 0,
  });
  const data = await ctx.api.get(
    `/api/admin/documents?archived=${state.archived}`,
  );
  let container;
  const render = () => {
    const rows = data.filter(
      (d) =>
        d.purpose === "knowledge" && (!state.branch ||
          d.branch_codes.includes("*") ||
          d.branch_codes.includes(state.branch)) &&
        d.title.toLowerCase().includes(state.search.toLowerCase()),
    );
    const columns = [
      [
        "Документ",
        (d) =>
          `<div class="person"><span class="resource-icon">${icon("document")}</span><div><strong>${e(d.title)}</strong><span class="cell-sub">${e(d.filename)} · версия ${d.version}</span>${!state.archived ? button("Публикация", "publication", d.id) : ""}</div></div>`,
      ],
      ["Доступ", (d) => e(scopeLabel(ctx, d.branch_codes))],
      [
        "Обработка",
        (d) =>
          badge(d.status) +
          (d.last_error ? `<p class="cell-sub">${e(d.last_error)}</p>` : ""),
      ],
      [
        "Публикация",
        (d) =>
          `<span class="badge ${d.published ? "green" : ""}">${d.published ? "Опубликован" : "Не опубликован"}</span><span class="cell-sub">${d.auto_publish ? "Автоматически после готовности" : "Ручное подтверждение"}</span>`,
      ],
      ["Добавлен", (d) => date(d.created_at)],
      [
        "",
        (d) =>
          `<div class="actions">${state.archived ? iconButton("restore", "Восстановить документ", "restore", d.id) : iconButton("download", "Скачать документ", "download", d.id) + iconButton("edit", "Изменить документ", "edit", d.id) + iconButton("copy", "Загрузить новую версию", "version", d.id) + (d.status === "failed" ? button("Повторить обработку", "reindex", d.id) : "") + iconButton("trash", "Удалить документ", "archive", d.id)}</div>`,
      ],
    ];
    const region = container.querySelector("[data-list]");
    region.innerHTML = table(
      columns,
      rows,
      state,
      empty(
        "Документов не найдено",
        state.archived
          ? "Удалённые документы можно будет восстановить здесь."
          : "Загрузите регламент, памятку или презентацию. HR сможет обновлять базу без разработчиков.",
        state.archived ? "" : "upload",
        "Загрузить документы",
      ),
    );
    bindPagination(region, state, render);
  };
  return {
    html:
      heading(
        "Документы",
        "Актуальные регламенты и материалы, на которые опирается помощник.",
        button("Загрузить документы", "upload", "", "primary") +
          button("Обновить", "refresh"),
      ) +
      toolbar("Название документа", ctx.refs.branches, state) +
      "<div data-list></div>",
    bind: (target) => {
      container = target;
      render();
      bindFilters(target, state, render, () => ctx.navigate("documents"));
      delegate(target, async (action, id) => {
        const item = data.find((d) => d.id === id);
        if (action === "refresh") return ctx.navigate("documents");
        if (action === "upload" || action === "version")
          return uploadDocuments(ctx, action === "version" ? item : null);
        if (action === "download")
          return ctx.api.download(item.id, item.filename);
        if (action === "edit") return documentEditor(ctx, item);
        if (action === "publication") {
          drawer(
            "Публикация документа",
            `<p>Версия ${item.version}: ${item.published ? "действующая" : "ещё не опубликована"}.</p><p class="muted">Старая версия остаётся доступной до публикации новой. Возврат прежней версии выполняется такой же публикацией.</p>${checkbox("auto_publish", "Публиковать автоматически после успешной обработки", item.auto_publish)}${["ready", "ready_stub"].includes(item.status) && !item.published ? button("Сделать действующей версией", "publish-now", item.id, "primary") : ""}`,
            {
              bind: (form) =>
                delegate(form, async (action) => {
                  if (action !== "publish-now") return;
                  if (
                    !(await confirmAction(
                      "Сделать эту версию действующей?",
                      "Другие версии документа перестанут использоваться в новых ответах.",
                      "Опубликовать",
                    ))
                  )
                    return;
                  await ctx.api.post(
                    `/api/admin/documents/${item.id}/publish`,
                    { revision: item.revision },
                  );
                  document.querySelector("#drawer").close();
                  notify("Действующая версия изменена");
                  await ctx.refresh();
                }),
              submit: async (form) => {
                await ctx.api.put(
                  `/api/admin/documents/${item.id}/publication`,
                  {
                    revision: item.revision,
                    auto_publish: form.elements.auto_publish.checked,
                  },
                );
                notify("Правило публикации сохранено");
                await ctx.refresh();
              },
            },
          );
          return;
        }
        if (action === "reindex") {
          await ctx.api.post(`/api/documents/${id}/reindex`);
          notify("Повторная обработка запущена");
          await ctx.refresh();
          return;
        }
        if (
          action === "archive" &&
          !(await confirmAction(
            "Удалить документ из базы знаний?",
            "Он сразу перестанет использоваться в ответах бота. Файл и история версий сохранятся в архиве.",
            "Удалить",
            true,
          ))
        )
          return;
        if (action === "archive")
          await ctx.api.delete(`/api/admin/documents/${id}`);
        if (action === "restore")
          await ctx.api.post(`/api/admin/documents/${id}/restore`);
        notify(
          action === "restore"
            ? "Версия восстановлена. При необходимости опубликуйте её"
            : "Документ в архиве",
        );
        await ctx.refresh();
      });
    },
  };
}

function uploadDocuments(ctx, previous = null) {
  drawer(
    previous ? "Новая версия документа" : "Загрузить документы",
    `${previous ? `<div class="notice">${icon("info")}Предыдущая версия «${e(previous.title)}» останется доступной до успешной обработки новой. При ручной публикации потребуется отдельное подтверждение.</div>` : input("title", "Название (для одного файла)", "", "text", 'placeholder="Например, правила трудового распорядка"')}<label class="file-drop">${icon("document")}<br>Выберите файл${previous ? "" : "ы"} или перетащите сюда<input type="file" name="file" ${previous ? "" : "multiple"} required accept=".pdf,.docx,.pptx,.txt,.md,.png,.jpg,.jpeg"></label><p class="draft-note">PDF, DOCX, PPTX, TXT, MD и изображения. До 35 МБ на файл.</p>${previous ? "" : checkbox("auto_publish", "Публиковать после успешной обработки", true) + scopes(ctx) + input("categories", "Категории сотрудников", "", "text", 'placeholder="office, production"', "Пусто — доступно всем категориям выбранных площадок.")}<div class="form-error" data-upload-progress></div>`,
    {
      submitLabel: previous ? "Заменить документ" : "Загрузить",
      submit: async (form) => {
        const files = [...form.elements.file.files];
        if (!files.length) throw new Error("Выберите файл");
        if (files.some((f) => f.size > 35 * 1024 * 1024))
          throw new Error("Размер каждого файла должен быть не больше 35 МБ");
        const branchCodes = previous ? previous.branch_codes : readScopes(form);
        for (let i = 0; i < files.length; i++) {
          const body = new FormData();
          body.append("file", files[i]);
          if (!previous) {
            body.append(
              "title",
              files.length === 1 && form.elements.title.value.trim()
                ? form.elements.title.value
                : files[i].name.replace(/\.[^.]+$/, ""),
            );
            body.append("branch_codes", JSON.stringify(branchCodes));
            body.append(
              "categories",
              JSON.stringify(csv(form.elements.categories.value)),
            );
            body.append("version", "1");
            body.append(
              "auto_publish",
              String(form.elements.auto_publish.checked),
            );
          }
          form.querySelector("[data-upload-progress]").textContent =
            `Загружаем ${i + 1} из ${files.length}: ${files[i].name}`;
          try {
            await ctx.api.post(
              previous
                ? `/api/admin/documents/${previous.id}/version`
                : "/api/documents",
              body,
            );
          } catch (error) {
            await ctx.loadRefs();
            throw new Error(
              `Загружено ${i} из ${files.length}. ${error.message}`,
            );
          }
        }
        notify(
          previous
            ? "Новая версия отправлена на обработку"
            : `Документов загружено: ${files.length}`,
        );
        await ctx.refresh();
      },
    },
  );
}

function documentEditor(ctx, item) {
  drawer(
    "Настройки документа",
    input("title", "Название", item.title, "text", "required") +
      scopes(ctx, item.branch_codes) +
      select(
        "activity_kind",
        "Тип активности",
        [
          { value: "survey", label: "Опрос / анкета" },
          { value: "quiz", label: "Тест с правильными ответами" },
        ],
        item.definition?.kind || "survey",
        null,
      ) +
      input(
        "pass_percent",
        "Проходной процент для теста",
        item.definition?.pass_percent ?? 70,
        "number",
        'min="0" max="100" required',
      ) +
      input(
        "categories",
        "Категории сотрудников",
        item.categories.join(", "),
        "text",
        "",
        "Пусто — все категории.",
      ) +
      `<div class="notice">${icon("info")}Статус обработки: ${badge(item.status)}. При изменении доступа бот сразу применит новые ограничения.</div>`,
    {
      submit: async (form) => {
        await ctx.api.put(`/api/admin/documents/${item.id}`, {
          title: form.elements.title.value,
          branch_codes: readScopes(form),
          categories: csv(form.elements.categories.value),
          revision: item.revision,
        });
        notify("Настройки документа сохранены");
        await ctx.refresh();
      },
    },
  );
}

export const scopeLabel = (ctx, codes) =>
  codes.includes("*")
    ? "Все площадки"
    : codes
        .map(
          (code) =>
            ctx.refs.branches.find((b) => b.code === code)?.name || code,
        )
        .join(", ");

export async function surveysPage(ctx) {
  const state = (ctx.states.surveys ||= {
    search: "",
    archived: false,
    page: 0,
  });
  const rows = await ctx.api.get(
    `/api/admin/surveys?archived=${state.archived}`,
  );
  const renderCards = () =>
    rows
      .filter((r) => r.title.toLowerCase().includes(state.search.toLowerCase()))
      .map(
        (t) =>
          `<article class="card resource-card"><div class="card-header"><div class="resource-icon">${icon("survey")}</div><span class="badge purple">Вопросов: ${t.question_count}</span></div><h3>${e(t.title)}</h3><p>${e(t.description || "Опрос с сохранением ответов и напоминаниями")}</p><div class="tag-list"><span class="tag">${e(scopeLabel(ctx, t.branch_codes))}</span><span class="tag">Редакция ${t.revision}</span></div><footer>${!state.archived ? button("Отправить", "send", t.id, "primary") : '<span class="muted">В архиве</span>'}<div class="actions">${state.archived ? iconButton("restore", "Восстановить опросник", "restore", t.id) : iconButton("edit", "Редактировать опросник", "edit", t.id) + iconButton("copy", "Создать копию", "clone", t.id) + iconButton("trash", "В архив", "archive", t.id)}</div></footer></article>`,
      )
      .join("");
  return {
    html:
      heading(
        "Опросники",
        "Создавайте вопросы один раз и отправляйте всей команде, отделу или сотруднику.",
        button("Создать опросник", "create", "", "primary"),
      ) +
      toolbar("Найти опросник", [], state) +
      `<div class="grid-3" data-cards>${renderCards() || empty("Опросников пока нет", "Соберите первый опрос в визуальном конструкторе.", "create", "Создать опросник")}</div>`,
    bind: (target) => {
      bindFilters(
        target,
        state,
        () =>
          (target.querySelector("[data-cards]").innerHTML =
            renderCards() ||
            empty("Ничего не найдено", "Попробуйте другое название.")),
        () => ctx.navigate("surveys"),
      );
      delegate(target, async (action, id) => {
        if (action === "create" || action === "edit")
          return surveyEditor(ctx, id);
        if (action === "send")
          return ctx.campaignEditor({ template_id: id, kind: "survey" });
        if (action === "clone") {
          await ctx.api.post(`/api/admin/surveys/${id}/clone`);
          notify("Копия опросника создана");
          return ctx.refresh();
        }
        if (
          action === "archive" &&
          !(await confirmAction(
            "Архивировать опросник?",
            "Уже отправленные опросы и ответы сохранятся. Новые рассылки по этому шаблону будут недоступны.",
            "В архив",
            true,
          ))
        )
          return;
        if (action === "archive")
          await ctx.api.delete(`/api/admin/surveys/${id}`);
        if (action === "restore")
          await ctx.api.post(`/api/admin/surveys/${id}/restore`);
        notify("Опросник обновлён");
        await ctx.refresh();
      });
    },
  };
}

const newQuestion = () => ({
  id: "q_" + crypto.randomUUID().replaceAll("-", "").slice(0, 12),
  text: "",
  kind: "rating",
  minimum: 1,
  maximum: 10,
  comment_below: 8,
  comment_prompt: "Расскажите, пожалуйста, подробнее.",
  options: [],
  correct_options: [],
  points: 1,
});
const newOption = () => ({
  id: "o_" + crypto.randomUUID().replaceAll("-", "").slice(0, 10),
  label: "",
  needs_comment: false,
  notify_hr: false,
});

export async function surveyEditor(ctx, id) {
  const item = ctx.refs.surveys.find((t) => t.id === id) || {};
  const questions = structuredClone(
    item.definition?.questions || [newQuestion()],
  );
  drawer(
    id ? "Редактировать опросник" : "Новый опросник",
    input(
      "title",
      "Название",
      item.title || "",
      "text",
      'required placeholder="Как проходит адаптация?"',
    ) +
      textarea("description", "Для чего этот опрос", item.description || "") +
      scopes(ctx, item.branch_codes) +
      select(
        "activity_kind",
        "Тип активности",
        [
          { value: "survey", label: "Опрос / анкета" },
          { value: "quiz", label: "Тест с правильными ответами" },
        ],
        item.definition?.kind || "survey",
        null,
      ) +
      input(
        "pass_percent",
        "Проходной процент для теста",
        item.definition?.pass_percent ?? 70,
        "number",
        'min="0" max="100" required',
      ) +
      input(
        "reminder_days",
        "Напомнить через, дней",
        (item.definition?.reminder_days || []).join(", "),
        "text",
        "",
        "Например: 1, 2. Пусто — без напоминаний.",
      ) +
      '<div class="form-section"><div class="card-header"><h3>Вопросы</h3>' +
      button("Предпросмотр", "preview") +
      "</div><div data-questions></div>" +
      button("Добавить вопрос", "add-question") +
      "<div data-survey-preview hidden></div></div>",
    {
      subtitle: "Изменения не затронут уже начатые опросы",
      bind: (form) => {
        const render = () => {
          form.querySelector("[data-questions]").innerHTML = questions
            .map(
              (q, i) =>
                `<article class="question-card" data-q="${i}"><div class="editor-head"><strong>Вопрос ${i + 1}</strong><div class="editor-tools">${iconButton("up", "Переместить выше", "q-up", i)}${iconButton("down", "Переместить ниже", "q-down", i)}${iconButton("trash", "Удалить вопрос", "q-remove", i)}</div></div><div class="field"><label for="question-text-${i}">Текст вопроса</label><textarea id="question-text-${i}" data-q-field="text" required>${e(q.text)}</textarea></div>${form.elements.activity_kind.value === "quiz" ? `<div class="field"><label>Баллы за правильный ответ</label><input type="number" data-q-field="points" value="${q.points ?? 1}" min="1" max="100" required></div>` : ""}<div class="field"><label for="question-kind-${i}">Тип ответа</label><select id="question-kind-${i}" data-q-field="kind">${[
                  ["single", "Один вариант"],
                  ["multiple", "Несколько вариантов"],
                  ["rating", "Числовая оценка"],
                  ["text", "Свободный текст"],
                ]
                  .filter(
                    ([value]) =>
                      form.elements.activity_kind.value !== "quiz" ||
                      ["single", "multiple"].includes(value),
                  )
                  .map(
                    ([value, label]) =>
                      `<option value="${value}" ${q.kind === value ? "selected" : ""}>${label}</option>`,
                  )
                  .join(
                    "",
                  )}</select></div>${["single", "multiple"].includes(q.kind) ? `<div data-options>${q.options.map((o, j) => `<div data-o="${j}"><div class="option-row"><input data-option-field="label" aria-label="Вариант ${j + 1}" value="${e(o.label)}" required placeholder="Вариант ответа">${iconButton("close", "Удалить вариант", "o-remove", `${i}:${j}`)}</div><div class="option-options">${form.elements.activity_kind.value === "quiz" ? `<label class="check"><input type="checkbox" data-correct-option="${e(o.id)}" ${(q.correct_options || []).includes(o.id) ? "checked" : ""}>Правильный ответ</label>` : ""}<label class="check"><input type="checkbox" data-option-field="needs_comment" ${o.needs_comment ? "checked" : ""}>Запросить комментарий</label><label class="check"><input type="checkbox" data-option-field="notify_hr" ${o.notify_hr ? "checked" : ""}>Сигнал HR</label></div></div>`).join("")}</div>${button("Добавить вариант", "o-add", i)}` : q.kind === "rating" ? `<div class="field-grid"><div class="field"><label>Минимум</label><input type="number" data-q-field="minimum" required value="${q.minimum}" min="0" max="99"></div><div class="field"><label>Максимум</label><input type="number" data-q-field="maximum" required value="${q.maximum}" min="1" max="100"></div><div class="field"><label>Запросить комментарий при оценке ниже</label><input type="number" data-q-field="comment_below" value="${q.comment_below ?? ""}" placeholder="Пусто — не запрашивать"></div><div class="field"><label>Уточняющий вопрос</label><input data-q-field="comment_prompt" value="${e(q.comment_prompt || "")}"></div></div>` : '<p class="draft-note">Сотрудник сможет написать свободный ответ. Он поступит в модуль анализа обратной связи.</p>'}</article>`,
            )
            .join("");
        };
        const activityKind = () => {
          const quiz = form.elements.activity_kind.value === "quiz";
          form.elements.pass_percent.closest(".field").hidden = !quiz;
          if (quiz)
            questions.forEach((q) => {
              if (!["single", "multiple"].includes(q.kind)) {
                q.kind = "single";
                q.options = [newOption(), newOption()];
                q.correct_options = [];
              }
            });
          render();
        };
        form.elements.activity_kind.onchange = activityKind;
        activityKind();
        const update = (event) => {
          const card = event.target.closest("[data-q]");
          if (!card) return;
          const q = questions[Number(card.dataset.q)],
            field = event.target.dataset.qField;
          if (field) {
            q[field] = [
              "minimum",
              "maximum",
              "comment_below",
              "points",
            ].includes(field)
              ? event.target.value === ""
                ? null
                : Number(event.target.value)
              : event.target.value;
            if (field === "kind") {
              if (["single", "multiple"].includes(q.kind) && !q.options.length)
                q.options = [newOption(), newOption()];
              render();
            }
          }
          if (event.target.dataset.correctOption) {
            const chosen = new Set(q.correct_options || []),
              value = event.target.dataset.correctOption;
            if (event.target.checked) chosen.add(value);
            else chosen.delete(value);
            q.correct_options =
              q.kind === "single" && event.target.checked
                ? [value]
                : [...chosen];
            if (q.kind === "single") render();
          }
          const option = event.target.closest("[data-o]");
          if (option && event.target.dataset.optionField)
            q.options[Number(option.dataset.o)][
              event.target.dataset.optionField
            ] =
              event.target.type === "checkbox"
                ? event.target.checked
                : event.target.value;
        };
        form.addEventListener("input", update);
        form.addEventListener("change", (event) => {
          if (event.target.tagName === "SELECT") update(event);
        });
        delegate(form, (action, raw) => {
          const [i, j] = raw.split(":").map(Number);
          if (action === "add-question") questions.push(newQuestion());
          if (action === "q-remove") {
            if (questions.length === 1)
              throw new Error("В опросе должен остаться хотя бы один вопрос");
            questions.splice(i, 1);
          }
          if (action === "q-up" && i > 0)
            [questions[i - 1], questions[i]] = [questions[i], questions[i - 1]];
          if (action === "q-down" && i < questions.length - 1)
            [questions[i + 1], questions[i]] = [questions[i], questions[i + 1]];
          if (action === "o-add") questions[i].options.push(newOption());
          if (action === "o-remove") questions[i].options.splice(j, 1);
          if (action === "preview") {
            const region = form.querySelector("[data-survey-preview]");
            region.hidden = !region.hidden;
            region.innerHTML = `<div class="notice"><strong>Так сотрудник увидит вопросы</strong></div>${questions.map((q) => `<div class="bubble"><strong>${e(q.text || "Текст вопроса")}</strong>${q.options.length && ["single", "multiple"].includes(q.kind) ? '<div class="tag-list">' + q.options.map((o) => `<span class="tag">${e(o.label || "Вариант")}</span>`).join("") + "</div>" : `<p>${q.kind === "rating" ? `Оценка от ${q.minimum} до ${q.maximum}` : "Поле свободного ответа"}</p>`}</div>`).join("")}`;
            return;
          }
          render();
        });
        render();
      },
      submit: async (form) => {
        const body = {
          title: form.elements.title.value,
          description: form.elements.description.value,
          kind: form.elements.activity_kind.value,
          pass_percent: Number(form.elements.pass_percent.value),
          branch_codes: readScopes(form),
          questions: questions.map((q) => ({
            ...q,
            correct_options:
              form.elements.activity_kind.value === "quiz"
                ? (q.correct_options || []).filter((id) =>
                    q.options.some((o) => o.id === id),
                  )
                : [],
            options: ["single", "multiple"].includes(q.kind) ? q.options : [],
            comment_below: q.kind === "rating" ? q.comment_below : null,
          })),
          reminder_days: csv(form.elements.reminder_days.value).map(Number),
          data_mode: item.definition?.data_mode || "real",
          revision: item.revision || null,
        };
        if (id) await ctx.api.put(`/api/admin/surveys/${id}`, body);
        else await ctx.api.post("/api/admin/surveys", body);
        notify("Опросник сохранён");
        await ctx.refresh();
      },
    },
  );
}

export async function scenariosPage(ctx) {
  const state = (ctx.states.scenarios ||= { search: "", archived: false });
  const rows = await ctx.api.get(
    `/api/admin/scenarios?archived=${state.archived}`,
  );
  const cards = () =>
    rows
      .filter((s) => s.name.toLowerCase().includes(state.search.toLowerCase()))
      .map(
        (s) =>
          `<article class="card resource-card"><div class="card-header"><div class="resource-icon">${icon("scenario")}</div><span class="badge blue">Версия ${s.version}</span></div><h3>${e(s.name)}</h3><p>${s.definition.steps.length} шагов · ${s.definition.surveys.length} опросов</p><p>${e(scopeLabel(ctx, s.branch_codes))}</p><footer><span class="muted">${ctx.refs.branches.filter((b) => b.scenario_id === s.id).length} площадок</span><div class="actions">${state.archived ? iconButton("restore", "Восстановить сценарий", "restore", s.id) : button("Редактировать", "edit", s.id) + iconButton("copy", "Создать копию сценария", "clone", s.id) + iconButton("trash", "В архив", "archive", s.id)}</div></footer></article>`,
      )
      .join("");
  return {
    html:
      heading(
        "Сценарии адаптации",
        "Настройте последовательность шагов от приглашения до окончания адаптации.",
        button("Создать сценарий", "create", "", "primary"),
      ) +
      toolbar("Найти сценарий", [], state) +
      `<div class="grid-3" data-cards>${cards() || empty("Сценариев пока нет", "Создайте сценарий и назначьте его площадке.", "create", "Создать сценарий")}</div>`,
    bind: (target) => {
      bindFilters(
        target,
        state,
        () => (target.querySelector("[data-cards]").innerHTML = cards()),
        () => ctx.navigate("scenarios"),
      );
      delegate(target, async (action, id) => {
        const item = rows.find((r) => r.id === id);
        if (action === "create") return scenarioEditor(ctx);
        if (action === "edit") return scenarioEditor(ctx, item);
        if (action === "clone")
          return scenarioEditor(ctx, {
            ...structuredClone(item),
            id: undefined,
            name: item.name + " (копия)",
            version: 1,
          });
        if (
          action === "archive" &&
          !(await confirmAction(
            "Архивировать версию сценария?",
            "Если она назначена площадке, сначала выберите другую версию в настройках площадки.",
            "В архив",
            true,
          ))
        )
          return;
        if (action === "archive")
          await ctx.api.delete(`/api/admin/scenarios/${id}`);
        if (action === "restore")
          await ctx.api.post(`/api/admin/scenarios/${id}/restore`);
        notify("Сценарий обновлён");
        await ctx.refresh();
      });
    },
  };
}

function scenarioEditor(ctx, item = {}) {
  const steps = structuredClone(
    item.definition?.steps || [
      {
        id: "step_" + crypto.randomUUID().replaceAll("-", "").slice(0, 10),
        kind: "message",
        text: "",
        schedule: {
          anchor: "hire",
          days: 0,
          months: 0,
          at: "10:00",
          working_day: null,
          next_workday: false,
        },
        categories: [],
        branch_codes: [],
        resource_keys: [],
        document_ids: [],
        manual: false,
      },
    ],
  );
  const surveyMap = new Map(
    (item.definition?.surveys || []).map((s) => [s.id, s]),
  );
  ctx.refs.surveys.forEach((s) => surveyMap.set(s.definition.id, s.definition));
  const materials = new Map();
  ctx.refs.branches.forEach((b) =>
    Object.keys(b.resources).forEach((k) =>
      materials.set(k, b.resource_labels?.[k] || k),
    ),
  );
  drawer(
    item.id ? "Новая версия сценария" : "Новый сценарий",
    input("name", "Название", item.name || "", "text", "required") +
      scopes(ctx, item.branch_codes) +
      `<div class="notice">${icon("info")}Сохранение создаст новую версию. Выполненные шаги и начатые опросы сохранятся; назначенные площадки получат обновлённый план.</div><div data-steps></div>${button("Добавить шаг", "add-step")}`,
    {
      bind: (form) => {
        const render = () => {
          form.querySelector("[data-steps]").innerHTML = steps
            .map(
              (s, i) =>
                `<article class="step-card" data-step="${i}"><div class="editor-head"><strong>Шаг ${i + 1}</strong><div class="editor-tools">${iconButton("up", "Шаг выше", "step-up", i)}${iconButton("down", "Шаг ниже", "step-down", i)}${iconButton("trash", "Удалить шаг", "step-remove", i)}</div></div><div class="field"><label>Действие</label><select data-s-field="kind">${[
                  ["message", "Сообщение сотруднику"],
                  ["survey", "Опрос сотрудника"],
                  ["manager_reminder", "Напоминание руководителю"],
                  ["flow", "Последовательность действий"],
                ]
                  .map(
                    ([v, l]) =>
                      `<option value="${v}" ${s.kind === v ? "selected" : ""}>${l}</option>`,
                  )
                  .join(
                    "",
                  )}</select></div><div class="field"><label>Сообщение</label><textarea data-s-field="text" required>${e(s.text)}</textarea><small>${ctx.refs.reference.placeholders.map((p) => e(p.key) + " — " + e(p.description)).join("; ")}.</small></div>${s.kind === "survey" ? `<div class="field"><label>Опросник</label><select data-s-field="survey_id" required><option value="">Выберите опросник</option>${[...surveyMap.values()].map((q) => `<option value="${e(q.id)}" ${q.id === s.survey_id ? "selected" : ""}>${e(q.title)}</option>`).join("")}</select></div>` : ""}${s.kind === "flow" ? `<div class="form-section"><h3>Действия по порядку</h3>${(s.actions || []).map((n, j) => `<div class="question-card" data-node="${j}"><strong>${e(n.source_ref || n.id)} · ${e({ message: "Сообщение", choice: "Выбор", material: "Материал", survey: "Опрос", wait: "Время", employee_resource: "Персональный материал" }[n.kind] || n.kind)}</strong>${n.kind !== "material" && n.kind !== "wait" ? `<label>Текст<textarea data-node-field="text">${e(n.text || "")}</textarea></label>` : ""}${n.kind === "material" ? `<p>Материал ${e(n.material_key)} — настраивается в разделе «Материалы маршрута».</p>` : ""}${n.kind === "survey" ? `<details><summary>Вопросы опроса «${e(surveyMap.get(n.survey_id)?.title || n.survey_id)}»</summary>${(surveyMap.get(n.survey_id)?.questions || []).map((q, qi) => `<div class="question-card" data-flow-survey="${e(n.survey_id)}" data-flow-question="${qi}"><label>Вопрос ${qi + 1}<textarea data-flow-field="text" required>${e(q.text)}</textarea></label>${q.kind === "rating" ? `<div class="field-grid"><label>Минимум<input type="number" data-flow-field="minimum" value="${q.minimum}" min="0" max="99"></label><label>Максимум<input type="number" data-flow-field="maximum" value="${q.maximum}" min="1" max="100"></label><label>Комментарий при оценке ниже<input type="number" data-flow-field="comment_below" value="${q.comment_below ?? ""}"></label></div><label><input type="checkbox" data-flow-field="optional_comment_on_positive" ${q.optional_comment_on_positive ? "checked" : ""}>Предлагать необязательный комментарий при высокой оценке</label>` : ""}${(q.options || []).map((o, oi) => `<label>Вариант ${oi + 1}<input data-flow-option="${oi}" data-flow-field="label" required value="${e(o.label)}"></label><label>Реакция<textarea data-flow-option="${oi}" data-flow-field="reply">${e(o.reply || "")}</textarea></label><label><input type="checkbox" data-flow-option="${oi}" data-flow-field="needs_comment" ${o.needs_comment ? "checked" : ""}>Запрашивать комментарий</label><label><input type="checkbox" data-flow-option="${oi}" data-flow-field="exclusive" ${o.exclusive ? "checked" : ""}>Исключает другие варианты</label>`).join("")}</div>`).join("")}</details>` : ""}${(n.options || []).map((o, k) => `<label>Вариант ${k + 1}<input data-node-option="${k}" data-option-field="label" value="${e(o.label)}"></label><label>Реакция<textarea data-node-option="${k}" data-option-field="reply">${e(o.reply || "")}</textarea></label>`).join("")}</div>`).join("")}</div>` : ""}<div class="field-grid"><div class="field"><label>От какой даты считать</label><select data-schedule="anchor"><option value="hire" ${s.schedule.anchor === "hire" ? "selected" : ""}>Дата выхода</option><option value="probation_end" ${s.schedule.anchor === "probation_end" ? "selected" : ""}>Окончание адаптации</option></select></div><div class="field"><label>Время сотрудника</label><input type="time" data-schedule="at" required value="${e(s.schedule.at || "09:00")}"></div><div class="field"><label>Календарных месяцев</label><input type="number" data-schedule="months" value="${s.schedule.months || 0}" min="-12" max="24"></div><div class="field"><label>Календарных дней</label><input type="number" data-schedule="days" value="${s.schedule.days || 0}" min="-365" max="730"><small>Отрицательное число — до указанной даты.</small></div><div class="field"><label>По счёту рабочий день</label><input type="number" data-schedule="working_day" value="${s.schedule.working_day || ""}" placeholder="Пусто — календарная дата" min="1" max="365"></div><div class="field"><label>Категория сотрудников</label><select data-s-category><option value="">Все категории</option><option value="office" ${s.categories?.includes("office") ? "selected" : ""}>Офис</option><option value="production" ${s.categories?.includes("production") ? "selected" : ""}>Производство</option></select></div></div><label class="check"><input type="checkbox" data-s-field="manual" ${s.manual ? "checked" : ""}>Запускать только по действию рекрутера</label>${s.kind === "message" ? `<label class="check"><input type="checkbox" data-s-reply ${s.reply_to === "hr" ? "checked" : ""}>Следующий ответ сотрудника направить HR</label>` : ""}<details><summary class="muted">Материалы и дополнительные условия</summary><div class="spacer"></div><div class="field"><label>Вариант текста на «ты»</label><textarea data-s-field="informal_text">${e(s.informal_text || "")}</textarea></div><span class="field-label">Материалы площадки</span><div class="scope-grid">${[...materials].map(([key, label]) => `<label class="check"><input type="checkbox" data-s-resource="${e(key)}" ${s.resource_keys?.includes(key) ? "checked" : ""}>${e(label)}</label>`).join("") || "<small>Добавьте материалы в настройках площадки</small>"}</div><span class="field-label">Вложения</span>${ctx.refs.documents.map((d) => `<label class="check"><input type="checkbox" data-s-document="${e(d.id)}" ${s.document_ids?.includes(d.id) ? "checked" : ""}>${e(d.title)}</label>`).join("")}<span class="field-label">Только для площадок (пусто — все)</span><div class="scope-grid">${ctx.refs.branches.map((b) => `<label class="check"><input type="checkbox" data-s-branch="${e(b.code)}" ${s.branch_codes?.includes(b.code) ? "checked" : ""}>${e(b.name)}</label>`).join("")}</div></details></article>`,
            )
            .join("");
        };
        const update = (event) => {
          const card = event.target.closest("[data-step]");
          if (!card) return;
          const s = steps[Number(card.dataset.step)],
            target = event.target;
          const flowQuestion = target.closest("[data-flow-question]");
          if (flowQuestion && target.dataset.flowField) {
            const question = surveyMap.get(flowQuestion.dataset.flowSurvey)
              .questions[Number(flowQuestion.dataset.flowQuestion)];
            const owner =
              target.dataset.flowOption === undefined
                ? question
                : question.options[Number(target.dataset.flowOption)];
            owner[target.dataset.flowField] =
              target.type === "checkbox"
                ? target.checked
                : target.type === "number"
                  ? target.value === ""
                    ? null
                    : Number(target.value)
                  : target.value;
          }
          const nodeCard = target.closest("[data-node]");
          if (nodeCard) {
            const node = s.actions[Number(nodeCard.dataset.node)];
            if (target.dataset.nodeField)
              node[target.dataset.nodeField] = target.value;
            if (target.dataset.nodeOption !== undefined)
              node.options[Number(target.dataset.nodeOption)][
                target.dataset.optionField
              ] = target.value;
          }
          if (target.hasAttribute("data-s-reply"))
            s.reply_to = target.checked ? "hr" : "none";
          if (target.dataset.sField) {
            s[target.dataset.sField] =
              target.type === "checkbox" ? target.checked : target.value;
            if (target.dataset.sField === "kind") render();
          }
          if (target.dataset.schedule)
            s.schedule[target.dataset.schedule] = [
              "days",
              "months",
              "working_day",
            ].includes(target.dataset.schedule)
              ? target.value === ""
                ? target.dataset.schedule === "working_day"
                  ? null
                  : 0
                : Number(target.value)
              : target.value;
          if (target.hasAttribute("data-s-category"))
            s.categories = target.value ? [target.value] : [];
          for (const [attr, field] of [
            ["data-s-resource", "resource_keys"],
            ["data-s-document", "document_ids"],
            ["data-s-branch", "branch_codes"],
          ])
            if (target.hasAttribute(attr)) {
              const values = new Set(s[field] || []),
                value = target.getAttribute(attr);
              target.checked ? values.add(value) : values.delete(value);
              s[field] = [...values];
            }
        };
        form.addEventListener("input", update);
        form.addEventListener("change", (event) => {
          if (event.target.tagName === "SELECT") update(event);
        });
        delegate(form, (action, raw) => {
          const i = Number(raw);
          if (action === "add-step")
            steps.push({
              id:
                "step_" + crypto.randomUUID().replaceAll("-", "").slice(0, 10),
              text: "",
              kind: "message",
              schedule: { anchor: "hire", months: 0, days: 0, at: "09:00" },
              categories: [],
              branch_codes: [],
              resource_keys: [],
              document_ids: [],
              manual: false,
            });
          if (action === "step-remove") {
            if (steps.length === 1)
              throw new Error("В сценарии должен остаться шаг");
            steps.splice(i, 1);
          }
          if (action === "step-up" && i > 0)
            [steps[i - 1], steps[i]] = [steps[i], steps[i - 1]];
          if (action === "step-down" && i < steps.length - 1)
            [steps[i + 1], steps[i]] = [steps[i], steps[i + 1]];
          render();
        });
        render();
      },
      submit: async (form) => {
        const used = new Set(
          steps.flatMap((s) =>
            s.kind === "survey"
              ? [s.survey_id]
              : s.kind === "flow"
                ? s.actions
                    .filter((n) => n.kind === "survey")
                    .map((n) => n.survey_id)
                : [],
          ),
        );
        if (used.has("") || used.has(undefined))
          throw new Error("Для каждого шага с опросом выберите опросник");
        const body = {
          name: form.elements.name.value,
          version: item.version || 1,
          branch_codes: readScopes(form),
          steps: steps.map((s) => ({
            ...s,
            survey_id: s.kind === "survey" ? s.survey_id : null,
            informal_text: s.informal_text || null,
          })),
          surveys: [...used].map((id) => surveyMap.get(id)),
          revision: item.id ? item.version : null,
        };
        const result = item.id
          ? await ctx.api.put(`/api/admin/scenarios/${item.id}`, body)
          : await ctx.api.post("/api/admin/scenarios", body);
        notify(`Опубликована версия ${result.version}`);
        await ctx.refresh();
      },
    },
  );
}
