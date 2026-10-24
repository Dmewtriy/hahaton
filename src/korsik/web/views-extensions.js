import {e,heading,button,input,select,textarea,drawer,delegate,dateTime,notify} from "./components.js";

const options = (rows, id="id", label="name") => rows.map(x=>({value:x[id],label:x[label]}));
const url = id=>"/api/management/tasks/"+encodeURIComponent(id);
const when = x=>x?new Date(new Date(x).getTime()-new Date(x).getTimezoneOffset()*60000).toISOString().slice(0,16):"";
const states={assigned:"Назначено",in_progress:"В работе",blocked:"Есть препятствие",completed:"Выполнено",cancelled:"Отменено"};
const labels={accept:"Принять",block:"Есть препятствие",resume:"Возобновить",complete:"Указать результат",clarify:"Уточнить",update:"Изменить",cancel:"Отменить",reopen:"Вернуть в работу",answer_blocker:"Ответить на препятствие"};

export async function attachmentsPage(ctx) {
  const rows=(ctx.refs.documents||[]).filter(d=>d.purpose!=="knowledge"&&d.purpose!=="route_image");
  return {html:heading("Планы и вложения","Персональные файлы не индексируются в базе знаний.",button("Загрузить","upload","","primary"))+
    '<div class="card">'+rows.map(d=>'<div class="tick-row"><div><strong>'+e(d.title)+'</strong><p>'+e(d.purpose)+' · v'+d.version+'</p></div>'+button("Скачать","download",d.id)+(d.purpose==="personal_plan"?button("Отправить сотруднику","plan-send",d.id):"")+'</div>').join("")+'</div>',
    bind(target){delegate(target,async(action,id)=>{
      if(action==="download"){const d=rows.find(x=>x.id===id);await ctx.api.download(id,d.filename);return;}
      if(action==="plan-send") { const d=rows.find(x=>x.id===id); const requestKey=crypto.randomUUID();
        drawer("Отправить план",input("due_at","Дата и время — оставьте пустым для отправки сейчас","","datetime-local")+select("third_workday","Правило времени",[{value:"false",label:"Выбранное время или сейчас"},{value:"true",label:"Третий рабочий день, 09:00 — офис"}],"false",null),{submit:async form=>{const f=Object.fromEntries(new FormData(form));await ctx.api.post("/api/optional/employees/"+d.owner_employee_id+"/plan/send",{request_key:requestKey,due_at:f.due_at?new Date(f.due_at).toISOString():null,third_workday:f.third_workday==="true"});notify("План поставлен в очередь");}}); return; }
      if(action!=="upload")return;
      drawer("Загрузить файл",input("title","Название","","text","required")+
        select("purpose","Назначение",[{value:"personal_plan",label:"Персональный план"},{value:"card_image",label:"Изображение открытки"},{value:"event_attachment",label:"Вложение мероприятия"},{value:"meeting_attachment",label:"Вложение встречи"}],"personal_plan",null)+
        select("branch_code","Площадка",options(ctx.refs.branches,"code"))+
        select("employee_id","Сотрудник — только для плана",options(ctx.refs.employees))+
        input("file","Файл","","file","required"),{submit:async form=>{
          const data=new FormData(form);if(data.get("purpose")!=="personal_plan")data.delete("employee_id");
          await ctx.api.post("/api/optional/attachments",data);notify("Файл сохранён");await ctx.refresh();
        }});
    });}};
}

export async function planningPage(ctx) {
  const rows=await ctx.api.get("/api/optional/planning");
  const edit=(item={})=>{
    const statuses=item.kind==="meeting"?["draft","scheduled","cancelled","held"]:item.kind==="event"?["draft","published","archived"]:["draft"];
    drawer(item.id?"Изменить запись":"Мероприятие или встреча",
      select("kind","Вид",[{value:"event",label:"Мероприятие"},{value:"meeting",label:"Встреча"}],item.kind||"event",null)+
      input("title","Название",item.title||"","text","required")+textarea("description","Описание",item.description||"")+
      select("branch_code","Площадка",options(ctx.refs.branches,"code"),item.branch_code||"")+
      input("starts_at","Дата и время на этом компьютере",when(item.starts_at),"datetime-local")+
      input("timezone","Часовой пояс показа",item.timezone||Intl.DateTimeFormat().resolvedOptions().timeZone)+input("location","Место или ссылка",item.location||"")+
      select("status","Состояние",statuses.map(s=>({value:s,label:({draft:"Черновик",scheduled:"Назначена",published:"Опубликовано",cancelled:"Отменена",held:"Проведена",archived:"Архив"})[s]})),item.status||"draft",null)+
      '<fieldset><legend>Получатели — без выбора вся площадка</legend>'+(ctx.refs.employees||[]).map(p=>'<label class="check"><input type="checkbox" name="employee_ids" value="'+e(p.id)+'" '+(item.audience?.employee_ids?.includes(p.id)?"checked":"")+'>'+e(p.name)+'</label>').join("")+'</fieldset>'+
      '<fieldset><legend>Вложения</legend>'+(ctx.refs.documents||[]).filter(d=>d.purpose!=="personal_plan").map(d=>'<label class="check"><input type="checkbox" name="document_ids" value="'+e(d.id)+'" '+(item.document_ids?.includes(d.id)?"checked":"")+'>'+e(d.title)+'</label>').join("")+'</fieldset>'+
      '<p>Новая запись сначала сохраняется как черновик. Оповещение создаётся отдельно и требует подтверждения в «Рассылках». Изменение записи отменит её старые неотправленные оповещения.</p>',{
      submit:async form=>{
        const d=Object.fromEntries(new FormData(form));
        const participants=new FormData(form).getAll("employee_ids");
        const data={kind:d.kind,title:d.title,description:d.description,branch_code:d.branch_code,starts_at:d.starts_at?new Date(d.starts_at).toISOString():null,timezone:d.timezone,location:d.location,status:d.status,
          audience:participants.length?{mode:"employees",employee_ids:participants}:{mode:"branches",branch_codes:[d.branch_code]},document_ids:new FormData(form).getAll("document_ids"),revision:item.revision||null};
        if(item.id)await ctx.api.put("/api/optional/planning/"+item.id,data);else await ctx.api.post("/api/optional/planning",data);
        notify("Запись сохранена. Отправка не запущена");await ctx.refresh();
      }});
  };
  return {html:heading("Мероприятия и встречи","HR управляет расписанием и отдельно подтверждает оповещения.",button("Добавить","new","","primary"))+
    '<div class="card">'+rows.map(r=>'<div class="tick-row"><div><strong>'+e(r.title)+'</strong><p>'+e(r.status)+' · '+(r.starts_at?dateTime(r.starts_at):"Дата уточняется")+' · '+e(r.timezone)+'</p></div><div class="actions">'+button("Изменить","edit",r.id)+button("Оповестить","announce",r.id)+button("История","history",r.id)+'</div></div>').join("")+'</div>',
    bind(target){delegate(target,async(action,id)=>{
      const item=rows.find(r=>r.id===id);if(action==="new")edit();if(action==="edit")edit(item);
      if(action==="announce"){const c=await ctx.api.post("/api/optional/planning/"+id+"/announcement",{revision:item.revision});notify("Создан черновик оповещения");await ctx.navigate("campaigns");await ctx.campaignDetail(c.id);}
      if(action==="history"){const h=await ctx.api.get("/api/optional/planning/"+id+"/history");drawer("История",h.map(x=>'<p>Редакция '+x.revision+' · '+dateTime(x.created_at)+' · '+e(x.snapshot.status)+'</p>').join(""));}
    });}};
}

export async function createManagementTask(ctx,source=null) {
  const choices=await ctx.api.get("/api/management/assignees");
  if(!choices.length)throw new Error("Нет разрешённых назначений. Администратору нужно выдать полномочия на конкретного HR и площадку.");
  const requestKey=crypto.randomUUID();
  drawer("Выдать поручение",select("assignment","HR и площадка",choices.map((x,i)=>({value:i,label:x.name+" · "+x.branch_code})),"0",null)+
    input("title","Что сделать",source?.suggestion||"","text","required")+textarea("description","Описание",source?"Проверить тему "+source.topic+". Случаев: "+source.cases+"; сотрудников: "+source.employees:"","required")+
    textarea("expected_result","Ожидаемый результат","")+input("due_at","Срок — необязательно","","datetime-local"),{
    submit:async form=>{const d=Object.fromEntries(new FormData(form)), a=choices[Number(d.assignment)];
      await ctx.api.post("/api/management/tasks",{assignee_id:a.id,branch_code:a.branch_code,title:d.title,description:d.description,expected_result:d.expected_result,
        due_at:d.due_at?new Date(d.due_at).toISOString():null,request_key:requestKey,source_insight_id:source?.id||null,source_report_id:source?.report_id||null,mode:source?.mode||"real"});await ctx.refresh();}
  });
}

async function taskDetail(ctx,id) {
  const task=await ctx.api.get(url(id));
  const feed=await ctx.api.get("/api/management/conversations/"+task.conversation_id+"/entries?limit=50");
  let actions=[];
  if(task.status!=="cancelled"){
    if(task.can_execute&&task.status!=="completed")actions=task.status==="blocked"?["resume","complete"]:task.status==="assigned"?["accept","block","complete"]:["block","complete"];
    if(task.can_direct)actions=task.status==="completed"?["reopen"]:["clarify","update","cancel",...(task.status==="blocked"?["answer_blocker"]:[])];
  }
  const entry=x=>'<div class="tick-row"><div><strong>'+e(({assigned:"Назначено",...labels})[x.kind]||x.kind)+' · '+e(x.body.task_number||x.task_id.slice(0,8))+' · '+e(x.body.task_title||"")+'</strong><p>'+e(x.body.text||x.body.description||"")+'</p><small>'+dateTime(x.created_at)+'</small></div></div>';
  drawer(task.display_number+" · "+task.title,'<p>'+e(task.description)+'</p><p>Ожидается: '+e(task.expected_result)+'</p><p>'+e(states[task.status])+(task.overdue?" · Просрочено":"")+(task.mode==="demo"?" · ДЕМО":"")+'</p>'+
    (task.result?'<p>Результат: '+e(task.result.kind)+' · '+e(task.result.text)+'</p>':"")+
    '<div class="actions">'+actions.map(a=>button(labels[a],a)).join("")+'</div><h3>История диалога</h3><div data-feed>'+feed.items.map(entry).join("")+'</div>'+
    (feed.next_after?button("Ещё события","more"):"")+button("Наблюдение после изменения","followup"),{
    bind(form){let cursor=feed.next_after;delegate(form,async action=>{
      if(action==="more"){const next=await ctx.api.get("/api/management/conversations/"+task.conversation_id+"/entries?after="+cursor+"&limit=50");form.querySelector("[data-feed]").insertAdjacentHTML("beforeend",next.items.map(entry).join(""));cursor=next.next_after;form.querySelector('[data-action="more"]').hidden=cursor===null;
        await ctx.api.post("/api/management/conversations/"+task.conversation_id+"/read",{through_seq:next.last_seen_seq});return;}
      if(action==="followup"){const f=await ctx.api.get("/api/management/improvements/tasks/"+id+"/followup");drawer("Наблюдение",'<p>'+e(f.status)+'</p><p>'+e(f.note||"")+'</p>'+Object.entries(f.periods||{}).map(([k,v])=>'<p>'+e(k)+': '+(v.cases??"недостаточно данных")+' случаев за '+v.days+' дней</p>').join(""));return;}
      if(!actions.includes(action))return;
      const requestKey=crypto.randomUUID();
      drawer(labels[action],textarea("text","Описание / причина","")+
        (action==="complete"?select("result_kind","Вид результата",[{value:"process_adjusted",label:"Процесс скорректирован"},{value:"knowledge_updated",label:"Корпоративный документ обновлён"},{value:"no_change_needed",label:"Изменение не требуется"}],"process_adjusted",null)+
          select("document_id","Документ — только при обновлении знания",(ctx.refs.documents||[]).filter(d=>d.purpose==="knowledge"&&d.published).map(d=>({value:d.id,label:d.title+" · v"+d.version+" · "+d.status}))):"")+
        (action==="update"?input("title","Название",task.title)+textarea("description","Описание",task.description)+textarea("expected_result","Ожидаемый результат",task.expected_result)+input("due_at","Срок",when(task.due_at),"datetime-local"):""),{
        submit:async f=>{const d=Object.fromEntries(new FormData(f));const body={action,revision:task.revision,request_key:requestKey,text:d.text};
          if(action==="complete"){body.result_kind=d.result_kind;if(d.result_kind==="knowledge_updated"){const doc=ctx.refs.documents.find(x=>x.id===d.document_id);body.document_id=doc?.id;body.document_version=doc?.version;}}
          if(action==="update")Object.assign(body,{title:d.title,description:d.description,expected_result:d.expected_result,due_at:d.due_at?new Date(d.due_at).toISOString():null});
          await ctx.api.post(url(id)+"/commands",body);notify("Действие сохранено");await ctx.refresh();}
      });
    });}
  });
  if(feed.last_seen_seq)await ctx.api.post("/api/management/conversations/"+task.conversation_id+"/read",{through_seq:feed.last_seen_seq});
}

export async function managementPage(ctx) {
  const data=await ctx.api.get("/api/management/tasks");
  const conversations=await ctx.api.get("/api/management/conversations");
  return {html:heading(ctx.me.roles.includes("hr_head")?"Диалоги и поручения":"Поручения от руководства","Ответы относятся к конкретной задаче. Переписка с сотрудниками остаётся отдельной.",ctx.me.roles.includes("hr_head")?button("Выдать поручение","new","","primary"):"")+
    '<div class="card">'+conversations.map(c=>'<p><strong>'+e(c.partner_name)+'</strong> · новых событий: '+c.unread_count+' · открытых поручений: '+c.open_tasks+'</p>').join("")+'</div><div class="card" data-tasks>'+
    data.items.map(t=>'<div class="tick-row"><div><strong>'+e(t.display_number)+" · "+e(t.title)+'</strong><p>'+e(states[t.status])+" · "+e(t.assignee_name)+" · "+e(t.branch_code)+(t.overdue?" · Просрочено":"")+(t.assignee_unavailable?" · Исполнитель недоступен":"")+'</p></div>'+button("Открыть","detail",t.id)+'</div>').join("")+'</div>'+(data.next_after?button("Ещё поручения","more"):""),
    bind(target){delegate(target,async(action,id)=>{
      if(action==="new")await createManagementTask(ctx);if(action==="detail")await taskDetail(ctx,id);
      if(action==="more"){const next=await ctx.api.get("/api/management/tasks?after="+data.next_after);data.items.push(...next.items);data.next_after=next.next_after;target.querySelector("[data-tasks]").insertAdjacentHTML("beforeend",next.items.map(t=>'<p>'+e(t.title)+button("Открыть","detail",t.id)+'</p>').join(""));if(!data.next_after)target.querySelector('[data-action="more"]').hidden=true;}
    });}};
}

export async function improvementsPage(ctx) {
  let report;try{report=await ctx.api.post("/api/management/improvements/reports");}catch(error){if(error.status!==409)throw error;return {html:heading("Что улучшить","Для отчёта нужна согласованная политика.")+'<div class="notice">'+e(error.message)+'</div>'};}
  return {html:heading("Что улучшить",(report.mode==="demo"?"ДЕМОНСТРАЦИОННЫЕ ДАННЫЕ · ":"")+dateTime(report.start)+" — "+dateTime(report.end))+
    '<div class="notice">'+e(report.coverage.note)+". Процессные правила: "+e(report.coverage.process_rules)+'. Имена и цитаты не раскрываются. Доли без знаменателя не рассчитываются.</div><div class="card">'+
    (report.items.map(x=>'<div class="tick-row"><div><strong>'+e(x.topic)+'</strong><p>'+e(x.category)+" · "+e(x.branch_code)+" · случаев: "+x.cases+"; сотрудников: "+x.employees+'</p></div><div class="actions">'+
      (x.work_in_progress?"По теме уже ведётся работа":button("Выдать поручение","task",x.id))+button("Отложить на 7 дней","snooze",x.id)+'</div></div>').join("")||"Нет кандидатов, проходящих порог. Это не означает отсутствие проблем.")+'</div>',
    bind(target){delegate(target,async(action,id)=>{
      if(action==="snooze"){await ctx.api.post("/api/management/improvements/insights/"+id+"/snooze");await ctx.refresh();}
      if(action==="task")await createManagementTask(ctx,{...report.items.find(x=>x.id===id),report_id:report.id,mode:report.mode});
    });}};
}

export async function grantsPage(ctx) {
  const rows=await ctx.api.get("/api/management/grants");
  const identities=await ctx.api.get("/api/identities");
  const sites=await ctx.api.get("/api/branches");
  const people=Object.fromEntries(identities.map(p=>[p.id,p.name]));
  return {html:heading("Полномочия на поручения","Разрешение выдаётся на конкретную пару руководитель–HR и площадку.",button("Настроить разрешение","grant","","primary")+ (ctx.me.branch_codes.includes("*")?button("Политика улучшений","policy"):""))+
    '<div class="card">'+rows.map(r=>'<p>'+e(people[r.issuer_id]||r.issuer_id)+" → "+e(people[r.assignee_id]||r.assignee_id)+" · "+e(r.branch_code)+" · "+(r.active?"действует":"отозвано")+'</p>').join("")+'</div>',
    bind(target){delegate(target,async action=>{if(action==="policy") { const policy=await ctx.api.get("/api/management/improvements/policy"); drawer("Политика улучшений",
      select("enabled","Состояние",[{value:"false",label:"Отключено"},{value:"true",label:"Включено"}],String(policy.enabled),null)+
      select("mode","Данные",[{value:"real",label:"Реальные — требуется согласование"},{value:"demo",label:"Демонстрационные"}],policy.mode,null)+
      input("min_employees","Минимум разных сотрудников",policy.min_employees,"number","min=3 max=100 required")+
      input("window_days","Окно анализа, дней",policy.window_days,"number","min=1 max=30 required")+
      input("approved_by","Кто согласовал — через запятую",policy.approved_by.join(", "))+
      input("approved_topics","Разрешённые темы — через запятую",policy.approved_topics.join(", "))+
      '<p>Условия конкретных вопросов опроса настраиваются отдельно по точной версии через документированный API. Низкая оценка сама по себе не доказывает причину.</p>',{submit:async form=>{const d=Object.fromEntries(new FormData(form));await ctx.api.put("/api/management/improvements/policy",{...policy,enabled:d.enabled==="true",mode:d.mode,min_employees:Number(d.min_employees),window_days:Number(d.window_days),approved_by:d.approved_by.split(",").map(x=>x.trim()).filter(Boolean),approved_topics:d.approved_topics.split(",").map(x=>x.trim()).filter(Boolean)});notify("Политика сохранена");}});return;}
      if(action!=="grant")return;drawer("Разрешение на поручения",select("issuer_id","Руководитель HR",options(identities.filter(p=>p.active&&p.roles.includes("hr_head"))))+select("assignee_id","HR-исполнитель",options(identities.filter(p=>p.active&&p.roles.some(r=>["hr","hr_head"].includes(r)))))+select("branch_code","Площадка",options(sites,"code"))+select("active","Доступ",[{value:"true",label:"Разрешить"},{value:"false",label:"Отозвать"}],"true",null),{
      submit:async form=>{const d=Object.fromEntries(new FormData(form));await ctx.api.post("/api/management/grants",{...d,active:d.active==="true"});await ctx.refresh();}
    });});}};
}
