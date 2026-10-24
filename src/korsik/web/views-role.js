import {
  e,
  heading,
  button,
  dateTime,
  drawer,
  delegate,
  select,
  notify,
  empty,
} from "./components.js";

function table(columns, rows) {
  if (!rows.length)
    return `<div class="card">${empty("Пока нет данных", "Здесь появятся данные в вашей области доступа.")}</div>`;
  return `<div class="card table-card"><div class="table-wrap"><table><thead><tr>${columns.map(([label]) => `<th>${e(label)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${columns.map(([, render]) => `<td>${render(row)}</td>`).join("")}</tr>`).join("")}</tbody></table></div></div>`;
}

export async function teamPage(ctx) {
  const data = await ctx.api.get("/api/workspace/team");
  const phases = {
    active: "Адаптация",
    preboarding: "До выхода",
    completed: "Завершена",
    stopped: "Остановлена",
    invited: "Ожидает подключения",
  };
  return {
    html:
      heading("Моя команда", "Ваши сотрудники и их прогресс адаптации.") +
      `<div class="kpis"><div class="card kpi"><strong>${data.totals.employees}</strong><p>Сотрудников</p></div><div class="card kpi"><strong>${data.totals.active_surveys}</strong><p>Активных опросов</p></div><div class="card kpi"><strong>${data.totals.completed_surveys}</strong><p>Завершено за 7 дней</p></div></div>` +
      table(
        [
          ["Сотрудник", (x) => e(x.name)],
          ["Подразделение", (x) => e(x.department)],
          ["Дата выхода", (x) => e(x.hire_date)],
          [
            "Адаптация",
            (x) =>
              e(
                phases[x.onboarding_phase || x.onboarding_status] ||
                  x.onboarding_status,
              ),
          ],
          ["Текущий шаг", (x) => e(x.stage_label)],
        ],
        data.employees,
        { page: 0 },
      ),
  };
}

export async function meetingsPage(ctx) {
  const rows = await ctx.api.get("/api/workspace/meetings");
  const states = {
    pending: "Ожидает маршрутизации",
    awaiting_manager: "Нужно назначить руководителя",
    queued: "В очереди отправки",
    delivered: "Рекомендация доставлена",
  };
  return {
    html:
      heading(
        "Личные встречи",
        "Рекомендации обсудить адаптацию. Руководитель самостоятельно назначает и проводит встречу.",
      ) +
      table(
        [
          ["Сотрудник", (x) => e(x.employee_name)],
          ["Подразделение", (x) => e(x.department)],
          ["Рекомендация", (x) => e(x.recommendation)],
          ["Состояние", (x) => e(states[x.status] || x.status)],
          ["Создано", (x) => dateTime(x.created_at)],
        ],
        rows,
        { page: 0 },
      ),
  };
}

export async function digestsPage(ctx) {
  const rows = await ctx.api.get("/api/workspace/digests");
  return {
    html:
      heading(
        "Еженедельные сводки",
        "Сохранённые показатели, период и происхождение данных. Числа рассчитывает backend.",
      ) +
      table(
        [
          [
            "Период",
            (x) => `${dateTime(x.window.from)} — ${dateTime(x.window.to)}`,
          ],
          ["Ответов", (x) => x.sample.answers || 0],
          ["Ответивших сотрудников", (x) => x.sample.responding_employees || 0],
          ["", (x) => button("Открыть", "digest", x.id)],
        ],
        rows,
        { page: 0 },
      ),
    bind: (target) =>
      delegate(target, async (action, id) => {
        const data = await ctx.api.get(`/api/workspace/digests/${id}`);
        drawer(
          "Недельная сводка",
          `<div class="bubble" style="white-space:pre-wrap">${e(data.text)}</div><h3>Сравнение показателей с прошлой неделей</h3>${Object.entries(
            data.report.comparison || {},
          )
            .map(
              ([name, value]) =>
                `<p>${e({ completed_surveys: "Завершённых опросов", tickets_created: "Новых обращений", analysed_answers: "Проанализированных ответов", questions: "Вопросов" }[name] || name)}: ${value.previous} → ${value.current} (${value.delta > 0 ? "+" : ""}${value.delta})</p>`,
            )
            .join(
              "",
            )}<h3>Основания сводки</h3><p>Ответов: ${data.provenance.answers.length}; анализов: ${data.provenance.analyses.length}.</p><ul>${data.provenance.analyses.map((a) => `<li>Ответ ${e(a.answer_id)}, обработчик ${e(a.processor_version)}, темы: ${e(a.topics.join(", "))}</li>`).join("")}</ul>`,
        );
      }),
  };
}

export async function managerRulesPage(ctx) {
  const rows = await ctx.api.get("/api/admin/manager-rules");
  const name = (id) => ctx.refs.staff.find((s) => s.id === id)?.name || id;
  return {
    html:
      heading(
        "Назначение руководителей",
        "Правило HR + площадка применяется к новым анкетам и при сохранении без выбранного руководителя. Явный выбор в анкете имеет приоритет. Существующие назначения автоматически не меняются.",
        button("Добавить правило", "create", "", "primary"),
      ) +
      table(
        [
          ["HR", (x) => e(name(x.recruiter_id))],
          [
            "Площадка",
            (x) =>
              e(
                ctx.refs.branches.find((b) => b.code === x.branch_code)?.name ||
                  x.branch_code,
              ),
          ],
          ["Руководитель", (x) => e(name(x.manager_id))],
          [
            "",
            (x) =>
              button("Изменить", "edit", x.id) +
              button("Удалить", "delete", x.id),
          ],
        ],
        rows,
        { page: 0 },
      ),
    bind: (target) =>
      delegate(target, async (action, id) => {
        const item = rows.find((r) => r.id === id) || {};
        if (action === "delete") {
          if (
            !(await ctx.confirm(
              "Удалить правило?",
              "Существующие назначения сотрудников сохранятся.",
              "Удалить",
            ))
          )
            return;
          await ctx.api.delete(`/api/admin/manager-rules/${id}`);
          return ctx.refresh();
        }
        drawer(
          "Связь HR и руководителя",
          select(
            "branch_code",
            "Площадка",
            ctx.refs.branches.map((b) => ({ value: b.code, label: b.name })),
            item.branch_code,
            null,
          ) +
            select(
              "recruiter_id",
              "HR",
              ctx.refs.staff
                .filter((s) =>
                  s.roles.some((r) => ["hr", "hr_head"].includes(r)),
                )
                .map((s) => ({ value: s.id, label: s.name })),
              item.recruiter_id,
              null,
            ) +
            select(
              "manager_id",
              "Руководитель",
              ctx.refs.staff
                .filter((s) => s.roles.includes("manager"))
                .map((s) => ({ value: s.id, label: s.name })),
              item.manager_id,
              null,
            ),
          {
            submit: async (form) => {
              await ctx.api.put(
                item.id
                  ? `/api/admin/manager-rules/${item.id}`
                  : "/api/admin/manager-rules",
                Object.fromEntries(new FormData(form)),
              );
              notify("Правило сохранено");
              await ctx.refresh();
            },
          },
        );
      }),
  };
}
