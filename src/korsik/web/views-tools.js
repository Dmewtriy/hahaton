import {
  e,
  heading,
  button,
  select,
  input,
  drawer,
  notify,
  delegate,
  empty,
  badge,
  dateTime,
} from "./components.js";

export async function reportsPage(ctx) {
  let previewHash = null;
  const catalogue = await ctx.api.get("/api/reports/surveys");
  const today = new Date().toISOString().slice(0, 10);
  const options = catalogue.map((s, i) => ({
    value: String(i),
    label: `${s.title} · ${s.version.slice(0, 8)}`,
  }));
  return {
    html:
      heading(
        "Результаты опросов",
        "Период выбирается по плановой дате назначения. Сохраняются частичные ответы; средние и доли рассчитывает backend.",
      ) +
      (catalogue.length
        ? `<form data-report class="card"><div class="field-grid">${select("survey", "Опрос и версия", options, "0", null)}${input("from_date", "С даты", today.slice(0, 4) + "-01-01", "date", "required")}${input("to_date", "По дату включительно", today, "date", "required")}${select("branch", "Площадка", [{ value: "", label: "Все доступные" }, ...ctx.refs.branches.map((b) => ({ value: b.code, label: b.name }))], "", null)}${select("department", "Подразделение", [{ value: "", label: "Все подразделения" }, ...ctx.refs.departments.map((d) => ({ value: d.name, label: d.name }))], "", null)}${select(
            "timezone",
            "Часовой пояс периода",
            ctx.refs.reference.timezones.map((t) => ({ value: t, label: t })),
            "Europe/Moscow",
            null,
          )}</div><label class="check"><input type="checkbox" name="include_archived">Включить сотрудников в архиве</label><div class="actions">${button("Предпросмотр", "preview", "", "primary")}${button("Скачать XLSX", "xlsx")}${button("Скачать CSV", "csv")}</div><p class="draft-note">Сотрудники видны по текущей области HR. Выгрузка не анонимная; ссылка скачивания действует 60 секунд.</p><div data-result role="status" aria-live="polite"></div></form>`
        : empty(
            "Назначенных опросов пока нет",
            "Назначьте опрос через рассылку или сценарий адаптации.",
          )),
    bind(target) {
      if (!catalogue.length) return;
      const form = target.querySelector("[data-report]");
      form.onsubmit = (event) => event.preventDefault();
      form.oninput = () => {
        previewHash = null;
      };
      form.elements.branch.onchange = () => {
        const code = form.elements.branch.value;
        form.elements.department.innerHTML =
          '<option value="">Все подразделения</option>' +
          ctx.refs.departments
            .filter((d) => !code || d.branch_code === code)
            .map((d) => `<option value="${e(d.name)}">${e(d.name)}</option>`)
            .join("");
      };
      delegate(target, async (action) => {
        if (!["preview", "xlsx", "csv"].includes(action)) return;
        if (!form.reportValidity()) return;
        const fields = Object.fromEntries(new FormData(form));
        const selected = catalogue[Number(fields.survey)];
        const data = {
          survey_key: selected.survey_key,
          version: selected.version,
          from_date: fields.from_date,
          to_date: fields.to_date,
          timezone: fields.timezone,
          branch_codes: fields.branch ? [fields.branch] : [],
          departments: fields.department ? [fields.department] : [],
          include_archived: form.elements.include_archived.checked,
          format: action === "csv" ? "csv" : "xlsx",
        };
        if (action === "preview") {
          const result = await ctx.api.post("/api/reports/preview", data);
          previewHash = result.preview_hash;
          target.querySelector("[data-result]").innerHTML =
            `<h3>Срез: ${dateTime(result.snapshot_at)}</h3><p>Назначений: ${result.totals.assignments}; сотрудников: ${result.totals.employees}; завершение: ${result.totals.completion_rate ?? "нет данных"}${result.totals.completion_rate === null ? "" : "%"}</p><div class="detail-list">${result.statistics.map((q) => `<div><span>${e(q.text)} · ответов: ${q.n}</span><strong>${q.mean === undefined ? "Комментариев: " + q.comments : "Средняя оценка: " + (q.mean ?? "нет данных")}</strong></div>`).join("")}</div><p class="draft-note">CSV: строки назначений и ответов. XLSX: результаты, числовая сводка, описание расчёта и полный текст вопросов.</p>`;
        } else {
          const file = await ctx.api.post("/api/reports/files", {
            ...data,
            preview_hash: previewHash,
          });
          await ctx.api.downloadFile(file.url, file.filename);
          notify("Выгрузка сформирована");
        }
      });
    },
  };
}

export async function processingPage(ctx) {
  const data = await ctx.api.get("/api/admin/feedback");
  const names = {
    completed: "Готово",
    pending: "В очереди",
    retry: "Повторная попытка",
    running: "Обрабатывается",
    failed: "Ошибка обработки",
    missing: "Задание не создано",
    waiting_binding: "Нет привязки MAX",
    not_created: "Уведомление не создано",
  };
  const finished = data.answers.filter((a) => a.status === "completed").length;
  return {
    html:
      heading(
        "Обработка обратной связи",
        "Сохранённый ответ не теряется при ошибке ML. Доставка HR и регистрация обращения учитываются отдельно.",
        button("Обновить", "refresh"),
      ) +
      `<div class="notice">Ответов для анализа: ${data.answers.length}; обработано: ${finished}; ожидают или требуют внимания: ${data.answers.length - finished}.</div><div class="card"><h2>Анализ ответов</h2>${data.answers.map((a) => `<div class="tick-row"><div><strong>${e(a.employee_name)}</strong><p>${e(a.survey_title)} · ${dateTime(a.created_at)}</p><p>${e(names[a.status] || a.status)}${a.version ? " · " + e(a.version) : ""}</p></div>${["failed", "missing"].includes(a.status) ? button("Повторить обработку", "retry", a.answer_id) : ""}</div>`).join("") || empty("Нет ответов для анализа", "Здесь появятся свободные ответы и комментарии к опросам.")}</div><div class="card"><h2>Уведомления HR об обращениях</h2><p class="muted">«Отправлено» означает передачу в MAX, а не прочтение человеком.</p>${data.notifications.map((n) => `<div class="tick-row"><div><strong>${e(n.employee_name)} → ${e(n.recipient_name)}</strong><p>Обращение №${e(n.ticket_id.slice(0, 8))}</p></div>${names[n.status] ? e(names[n.status]) : badge(n.status)}</div>`).join("") || "<p>Уведомлений пока нет.</p>"}</div>`,
    bind(target) {
      delegate(target, async (action, id) => {
        if (action === "retry") {
          await ctx.api.post(`/api/admin/feedback/${id}/retry`);
          notify("Анализ снова поставлен в очередь");
        }
        await ctx.navigate("processing");
      });
    },
  };
}

export async function materialsPage(ctx) {
  const state = await ctx.api.get("/api/admin/route-readiness");
  const brand = await fetch("/admin-assets/brand/manifest.json").then(r => r.json());
  const images = Object.fromEntries(brand.bindings.map(b => [b.id, b.file]));
  return {
    html:
      heading(
        "Материалы маршрута",
        "Сценарий с обязательным отсутствующим материалом ожидает подготовки. Оригиналы файлов нужно загрузить в разделе «Документы».",
        button("Добавить изображения Корсика", "company-images", "", "primary") + button("Обновить", "refresh"),
      ) +
      `<div class="notice">Обязательных материалов не подготовлено: ${state.missing_required}. Изображения Корсика привязываются по номерам из ТЗ, только в пустые назначения. В исходном архиве нет открыток №1, №2 и фото мероприятий — их нужно загрузить отдельно.</div><div class="card"><div class="detail-list brand-materials">${state.materials.map((m) => `<div class="material-row">${images[m.key.split(":")[0]] ? `<img class="material-thumb" src="/admin-assets/brand/${e(images[m.key.split(":")[0]])}" alt="Образец из ТЗ" loading="lazy">` : ""}<span><strong>${e(m.title)}</strong><small class="cell-sub">${e(m.key)} · ${e(m.branch_codes.join(", "))} · ${m.required ? "обязательный" : "дополнительный"}</small></span><span>${badge(m.ready ? "ready" : "waiting_material")}${m.can_edit ? button("Настроить", "edit", m.key) : "<small>Общий материал — настройка у HR всей области</small>"}</span></div>`).join("") || empty("Реестр ещё не установлен", "Установите исходный маршрут v0.4 в настройках.")}</div></div><h2>Требуют внимания HR</h2><div class="card">${state.blocked.map((s) => `<div class="tick-row"><div><strong>${e(s.employee_name)} · ${e(s.step)}</strong><p>${e(s.error || "Пропущенный шаг: требуется решение HR о запуске")}</p></div>${s.status === "late_review" ? button("Запустить", "trigger", s.id) : badge(s.status)}</div>`).join("") || "<p>Заблокированных шагов нет.</p>"}</div>`,
    bind(target) {
      delegate(target, async (action, id) => {
        if (action === "company-images") {
          const result = await ctx.api.post("/api/admin/materials/company-images");
          notify("Изображения назначены: " + result.installed.length + ". Уже выбранные материалы сохранены.");
          await ctx.loadRefs();
          return ctx.navigate("materials");
        }
        if (action === "refresh") return ctx.navigate("materials");
        if (action === "trigger") {
          await ctx.api.post(`/api/steps/${id}/trigger`);
          notify("Шаг поставлен в очередь");
          return ctx.navigate("materials");
        }
        if (action === "edit") {
          const item = state.materials.find((m) => m.key === id);
          drawer(
            item.title,
            select(
              "document_id",
              "Загруженный документ",
              [
                { value: "", label: "Не назначен" },
                ...ctx.refs.documents
                  .filter((d) => d.active !== false)
                  .map((d) => ({ value: d.id, label: d.title })),
              ],
              item.document_id || "",
              null,
            ) +
              input(
                "owner",
                "Ответственный за материал",
                item.owner || ctx.me.name,
              ) +
              input(
                "url",
                "Или ссылка",
                item.url || "",
                "url",
                "",
                "Для карточек питания требуется PDF-документ. Для изображений прикрепите файл. Очистка обоих полей снимает назначение.",
              ),
            {
              submit: async (form) => {
                const fields = Object.fromEntries(new FormData(form));
                await ctx.api.put(
                  `/api/admin/materials/${encodeURIComponent(id)}`,
                  {
                    document_id: fields.document_id || null,
                    url: fields.url || null,
                    revision: item.revision,
                    owner: fields.owner,
                  },
                );
                notify("Материал обновлён");
                await ctx.navigate("materials");
              },
            },
          );
        }
      });
    },
  };
}
