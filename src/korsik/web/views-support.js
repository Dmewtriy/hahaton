import {
  e,
  icon,
  heading,
  button,
  badge,
  empty,
  dateTime,
  textarea,
  select,
  drawer,
  notify,
  toolbar,
  bindFilters,
  table,
  bindPagination,
  delegate,
} from "./components.js";

const unreadDot = (count) =>
  count
    ? `<span class="unread-dot" role="img" aria-label="Новых сообщений: ${count}" title="Новых сообщений: ${count}"></span>`
    : "";

export async function ticketsPage(ctx) {
  const state = (ctx.states.tickets ||= {
    search: "",
    branch: "",
    archived: false,
    page: 0,
    scope: "mine",
    unreadOnly: false,
  });
  const retained = await ctx.api.get("/api/support/retained-cases");
  let rows = await ctx.api.get(
      `/api/admin/inbox?scope=${state.scope}&archived=${state.archived}`,
    ),
    container;
  const render = () => {
    const filtered = rows.filter(
      (row) =>
        (!state.branch || row.branch_code === state.branch) &&
        (!state.unreadOnly || row.unread_count) &&
        `${row.name} ${row.department}`
          .toLowerCase()
          .includes(state.search.toLowerCase()),
    );
    const list = container.querySelector("[data-list]");
    list.innerHTML = table(
      [
        [
          "Сотрудник",
          (row) =>
            `<div class="inbox-person">${unreadDot(row.unread_count)}<div><button class="link-button" data-action="thread" data-id="${e(row.employee_id)}"><strong>${e(row.name)}</strong></button><span class="cell-sub">${e(row.department)} · ${e(row.branch_name)}</span></div></div>`,
        ],
        [
          "Последнее сообщение",
          (row) =>
            `<span class="inbox-preview">${e(row.last_message || "Переписки пока нет")}</span><span class="cell-sub">${row.last_at ? dateTime(row.last_at) : ""}</span>`,
        ],
        [
          "Внимание",
          (row) =>
            row.unread_count
              ? `<span class="badge red">Новых: ${row.unread_count}</span>`
              : row.open_tickets
                ? `<span class="badge orange">Открытых обращений: ${row.open_tickets}</span>`
                : '<span class="muted">Всё просмотрено</span>',
        ],
        ...(state.scope === "all"
          ? [["Рекрутер", (row) => e(row.recruiter_name)]]
          : []),
      ],
      filtered,
      state,
      empty(
        state.unreadOnly ? "Новых сообщений нет" : "Сотрудников не найдено",
        state.scope === "mine"
          ? "Здесь отображаются закреплённые за вами сотрудники. Для обзора своей области выберите соответствующий фильтр."
          : "Измените поиск или добавьте сотрудников в разделе «Сотрудники».",
      ),
    );
    bindPagination(list, state, render);
  };
  const reload = async () => {
    rows = await ctx.api.get(
      `/api/admin/inbox?scope=${state.scope}&archived=${state.archived}`,
    );
    if (container.isConnected) render();
  };
  return {
    html:
      heading(
        "Переписка с сотрудниками",
        "Одна история на человека. Сообщения HR не останавливают опросы и рассылки.",
        button("Обновить", "refresh"),
      ) +
      `<div class="inbox-controls"><div class="segmented" aria-label="Область переписки"><button data-action="scope" data-id="mine" class="${state.scope === "mine" ? "active" : ""}">Мои сотрудники</button><button data-action="scope" data-id="all" class="${state.scope === "all" ? "active" : ""}">Вся моя область</button></div><label class="check"><input type="checkbox" data-unread-only ${state.unreadOnly ? "checked" : ""}>Только новые</label></div>` +
      toolbar("Имя или подразделение", ctx.refs.branches, state) +
      "<div data-list></div>" +
      (retained.length
        ? `<div class="card"><h2>Сохранённый доступ к обращениям</h2><p class="muted">История ранее закреплённых за вами обращений. Другие данные сотрудника за пределами вашей текущей области недоступны.</p>${retained.map((t) => `<div class="tick-row"><div><strong>${e(t.employee_name)}</strong><p>${e(t.reason)} · ${badge(t.status)}</p></div>${button("История обращения", "retained", t.id)}</div>`).join("")}</div>`
        : ""),
    bind: (target) => {
      container = target;
      render();
      bindFilters(target, state, render, () => ctx.navigate("tickets"));
      target.querySelector("[data-unread-only]").onchange = (event) => {
        state.unreadOnly = event.target.checked;
        state.page = 0;
        render();
      };
      delegate(target, async (action, id) => {
        if (action === "retained") return ticketDetail(ctx, id);
        if (action === "thread") return threadDetail(ctx, id);
        if (action === "scope") {
          state.scope = id;
          state.page = 0;
          return ctx.navigate("tickets");
        }
        if (action === "refresh") {
          await reload();
          await ctx.syncInbox();
        }
      });
      ctx.onInboxPoll = reload;
    },
  };
}

export async function ticketDetail(ctx, ticketId) {
  const ticket = await ctx.api.get(`/api/tickets/${ticketId}`);
  if (!ticket.can_reply) {
    drawer(
      "История обращения №" + ticket.id.slice(0, 8),
      `<div class="notice">Сохранён доступ только к этому обращению. Для работы с другими данными нужна действующая область HR.</div><h3>${e(ticket.reason)}</h3><p>${badge(ticket.status)}</p><div class="code">${e(ticket.context.question || ticket.context.message || "")}</div>${ticket.messages.map((m) => `<article class="bubble"><small>${dateTime(m.at)}</small><div>${e(m.text)}</div></article>`).join("")}${ticket.resolution ? `<h3>Предложенное решение</h3><p>${e(ticket.resolution)}</p>` : ""}`,
    );
    return;
  }
  return threadDetail(ctx, ticket.employee_id);
}

export async function threadDetail(ctx, employeeId) {
  const initial = await ctx.api.get(`/api/admin/inbox/${employeeId}`);
  let messages = initial.messages,
    hasOlder = initial.has_more,
    form,
    requestKey = null,
    requestText = null,
    readId = 0,
    loading = false;
  const lastId = () => messages.at(-1)?.id || 0;
  const atBottom = () => {
    const area = form.querySelector("[data-history]");
    return area.scrollHeight - area.scrollTop - area.clientHeight < 70;
  };
  const markSeen = async () => {
    if (
      !form?.isConnected ||
      !document.querySelector("#drawer").open ||
      !atBottom()
    )
      return;
    const id = lastId();
    if (id <= readId) return;
    await ctx.api.post(`/api/admin/inbox/${employeeId}/read`, {
      last_message_id: id,
    });
    readId = Math.max(readId, id);
    await ctx.syncInbox(false);
  };
  const renderMessages = (scrollBottom) => {
    const area = form.querySelector("[data-history]");
    area.innerHTML = messages.length
      ? messages
          .map(
            (message) =>
              `<article class="bubble ${message.kind === "hr" ? "hr" : message.kind === "signal" ? "inbox-signal" : message.kind === "system" ? "inbox-system" : ""}"><small>${e(message.kind === "signal" ? "Сигнал обратной связи" : message.author_name)} · ${dateTime(message.at)}</small><div>${e(message.text)}</div></article>`,
          )
          .join("")
      : '<div class="empty"><h3>Переписки пока нет</h3><p>Напишите сотруднику первое сообщение.</p></div>';
    form.querySelector("[data-older]").hidden = !hasOlder;
    if (scrollBottom) area.scrollTop = area.scrollHeight;
  };
  const fetchNew = async () => {
    if (
      loading ||
      !form?.isConnected ||
      !document.querySelector("#drawer").open
    )
      return;
    loading = true;
    try {
      const bottom = atBottom();
      const update = await ctx.api.get(
        `/api/admin/inbox/${employeeId}?after=${lastId()}`,
      );
      if (update.messages.length) {
        messages = [
          ...new Map(
            [...messages, ...update.messages].map((message) => [
              message.id,
              message,
            ]),
          ).values(),
        ].sort((a, b) => a.id - b.id);
        renderMessages(bottom);
        form.querySelector("[data-new-messages]").hidden = bottom;
      }
      await markSeen();
    } finally {
      loading = false;
    }
  };
  form = drawer(
    initial.name,
    `<p class="muted">${e(initial.department)} · вся история общения с HR</p><div class="inbox-thread-actions">${button("Обновить переписку", "refresh-thread")}${button("Загрузить предыдущие", "older")}<span data-new-messages hidden>${button("Новые сообщения ↓", "bottom")}</span>${initial.active ? button("Предложить решение", "resolve") : ""}</div>${select(
      "ticket_id",
      "Обращение для ответа",
      initial.cases
        .filter((t) => t.status !== "closed")
        .map((t) => ({
          value: t.id,
          label: `№${t.id.slice(0, 8)} · ${t.reason} · ${t.status === "awaiting_confirmation" ? "ожидает подтверждения" : "в работе"}`,
        })),
      initial.cases.filter((t) => t.status !== "closed").length === 1
        ? initial.cases.find((t) => t.status !== "closed").id
        : "",
      "Выберите обращение",
    )}<div class="inbox-history chat" data-history></div>${initial.active ? textarea("reply", "Сообщение сотруднику", "", 'required placeholder="Напишите ответ…"') : '<div class="notice">Сотрудник в архиве. История доступна для просмотра.</div>'}<p class="draft-note">Ответ отправится в MAX. Сотрудник сможет ответить через /hr. Его опросы продолжатся независимо от переписки.</p>`,
    {
      submitLabel: "Отправить",
      closeOnSubmit: false,
      submit: initial.active
        ? async (currentForm) => {
            const text = currentForm.elements.reply.value.trim();
            if (!text) throw new Error("Напишите сообщение");
            if (text !== requestText) {
              requestKey = crypto.randomUUID();
              requestText = text;
            }
            await ctx.api.post(
              `/api/admin/inbox/${employeeId}/messages`,
              { text, ticket_id: currentForm.elements.ticket_id.value || null },
              { "Idempotency-Key": requestKey },
            );
            currentForm.elements.reply.value = "";
            requestKey = null;
            requestText = null;
            await fetchNew();
            await ctx.syncInbox();
            notify("Сообщение отправлено");
          }
        : null,
      bind: (currentForm) => {
        delegate(currentForm, async (action) => {
          if (action === "refresh-thread") {
            await fetchNew();
            await ctx.syncInbox(false);
          }
          if (action === "older" && hasOlder) {
            const older = await ctx.api.get(
              `/api/admin/inbox/${employeeId}?before=${messages[0]?.id || 0}`,
            );
            hasOlder = older.has_more;
            const area = currentForm.querySelector("[data-history]"),
              previousHeight = area.scrollHeight;
            messages = [...older.messages, ...messages];
            renderMessages(false);
            area.scrollTop = area.scrollHeight - previousHeight;
          }
          if (action === "bottom") {
            const area = currentForm.querySelector("[data-history]");
            area.scrollTop = area.scrollHeight;
            currentForm.querySelector("[data-new-messages]").hidden = true;
            await markSeen();
          }
          if (action === "resolve") {
            const ticketId = currentForm.elements.ticket_id.value;
            if (!ticketId) throw new Error("Выберите одно обращение");
            const text = currentForm.elements.reply.value.trim();
            if (!text) throw new Error("Напишите решение в поле ответа");
            await ctx.api.post(
              `/api/support/${ticketId}/resolution`,
              { text },
              { "Idempotency-Key": crypto.randomUUID() },
            );
            currentForm.elements.reply.value = "";
            await fetchNew();
            await ctx.syncInbox();
            notify("Решение отправлено. Закрытие подтвердит сотрудник");
          }
        });
        currentForm.querySelector("[data-history]").onscroll = () => {
          if (atBottom()) {
            currentForm.querySelector("[data-new-messages]").hidden = true;
            markSeen().catch(() => {});
          }
        };
      },
    },
  );
  form.querySelector('[data-action="older"]').dataset.older = "";
  // Keep the history rendering independent from the reply form so polling cannot erase a draft.
  renderMessages(true);
  await markSeen();
  ctx.onThreadPoll = fetchNew;
}
