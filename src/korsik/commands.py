"""The public slash-menu; privileged staff commands remain role-checked in the bot."""

PUBLIC_COMMANDS = [
    {"name": "menu", "description": "Показать меню и доступные действия"},
    {"name": "hr", "description": "Написать HR: /hr сообщение или одно следующее сообщение"},
    {"name": "ask", "description": "Задать вопрос по документам компании"},
    {"name": "resume", "description": "Показать текущий вопрос опроса"},
    {"name": "cases", "description": "Мои обращения HR и их состояние"},
    {"name": "food", "description": "Информация о питании на вашей площадке"},
    {"name": "places", "description": "Поиск на карте рядом с рабочим адресом: /places кафе"},
    {"name": "materials", "description": "Материалы и полезные ссылки вашей площадки"},
    {"name": "cancel", "description": "Отменить ввод сообщения для HR или вопроса к ИИ"},
    {"name": "help", "description": "Помощь по работе с ботом"},
    {"name": "start", "description": "Начать работу или подключиться по приглашению"},
]
