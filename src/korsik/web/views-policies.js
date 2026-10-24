import {
  e,
  button,
  drawer,
  checkbox,
  select,
  input,
  csv,
  notify,
} from "./components.js";

export async function policyPanel(ctx) {
  const data = await ctx.api.get("/api/admin/policies");
  const escalation = data.escalation_rules,
    digest = data.digest_policy;
  const days = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
  ];
  const risks = {
    none: "Без риска",
    demo_risk: "Учебный сигнал",
    low: "Низкий",
    medium: "Средний",
    high: "Высокий",
  };
  return {
    html: `<div class="grid-2 form-section"><div class="card"><h2>Сигналы HR</h2><p class="muted">Анализ обратной связи: ${escalation.enabled ? "включён" : "выключен"}. Порог риска: ${risks[escalation.minimum_risk]}.</p><p class="muted">Неизвестный ответ всегда передаётся HR независимо от этого правила.</p>${data.can_edit ? button("Настроить сигналы", "policy-escalation") : "<small>Настраивает HR с доступом ко всем площадкам.</small>"}</div><div class="card"><h2>Еженедельный дайджест</h2><p class="muted">${digest.enabled ? `${days[digest.weekday]}, ${String(digest.hour).padStart(2, "0")}:00 · ${e(digest.timezone)}` : "Рассылка отключена"}.</p><p class="muted">Руководитель HR получает показатели своей области доступа.</p>${data.can_edit ? button("Настроить дайджест", "policy-digest") : "<small>Настраивает HR с доступом ко всем площадкам.</small>"}</div></div>`,
    handle(action) {
      if (action === "policy-escalation") {
        drawer(
          "Правила сигналов HR",
          checkbox(
            "enabled",
            "Создавать сигналы по результатам анализа",
            escalation.enabled,
          ) +
            select(
              "minimum_risk",
              "Минимальный риск для сигнала",
              Object.entries(risks).map(([value, label]) => ({ value, label })),
              escalation.minimum_risk,
              null,
            ) +
            input(
              "topics",
              "Ограничить сигналы темами",
              escalation.topics.join(", "),
              "text",
              "",
              "Коды тем согласуются с ML-командой. Пусто — все темы. Порог риска применяется в любом случае.",
            ),
          {
            submit: async (form) => {
              await ctx.api.put("/api/policies/escalation", {
                enabled: form.elements.enabled.checked,
                minimum_risk: form.elements.minimum_risk.value,
                topics: csv(form.elements.topics.value),
              });
              notify("Правила сигналов сохранены");
              await ctx.refresh();
            },
          },
        );
        return true;
      }
      if (action === "policy-digest") {
        drawer(
          "Еженедельный дайджест",
          checkbox(
            "enabled",
            "Отправлять еженедельную сводку руководителям HR",
            digest.enabled,
          ) +
            select(
              "weekday",
              "День недели",
              days.map((label, value) => ({ value, label })),
              digest.weekday,
              null,
            ) +
            input(
              "hour",
              "Час отправки",
              digest.hour,
              "number",
              'required min="0" max="23"',
            ) +
            input(
              "timezone",
              "Часовой пояс",
              digest.timezone,
              "text",
              "required",
              "Например, Europe/Moscow или Asia/Novosibirsk.",
            ),
          {
            submit: async (form) => {
              await ctx.api.put("/api/policies/digest", {
                enabled: form.elements.enabled.checked,
                weekday: Number(form.elements.weekday.value),
                hour: Number(form.elements.hour.value),
                timezone: form.elements.timezone.value,
              });
              notify("Расписание дайджеста сохранено");
              await ctx.refresh();
            },
          },
        );
        return true;
      }
      return false;
    },
  };
}

export function analyticsPanel(data) {
  const sections = [
    ["Темы обратной связи", data.totals.feedback_topics],
    ["Темы вопросов", data.totals.question_topics],
    ["Риски по ответам", data.totals.risks],
  ];
  const names = {
    none: "Без риска",
    demo_risk: "Учебный сигнал",
    low: "Низкий",
    medium: "Средний",
    high: "Высокий",
    access: "Доступы",
    equipment: "Оборудование",
    conflict: "Отношения в коллективе",
    tasks: "Рабочие задачи",
    other: "Другие темы",
  };
  return `<div class="grid-3 form-section">${sections
    .map(
      ([title, entries]) =>
        `<div class="card"><h2>${title}</h2>${
          Object.entries(entries || {})
            .map(
              ([key, count]) =>
                `<div class="card-row"><span>${e(names[key] || key)}</span><strong>${count}</strong></div>`,
            )
            .join("") || '<p class="muted">За этот период данных пока нет.</p>'
        }</div>`,
    )
    .join("")}</div>`;
}
