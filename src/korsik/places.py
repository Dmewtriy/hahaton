"""Explicit map search using the work address; no home address or model inference."""

from urllib.parse import urlencode


def search_link(branch, query):
    address = ", ".join(
        branch.workplace.get(k, "").strip()
        for k in ("city", "street", "building")
        if branch.workplace.get(k, "").strip()
    )
    if not address:
        return {
            "status": "no_address",
            "message": "HR ещё не указал рабочий адрес площадки. Напишите /hr для уточнения.",
            "url": None,
        }
    return {
        "status": "external_link",
        "address": address,
        "message": f"Поиск на внешней карте у рабочего адреса: {address}. Результаты и актуальность показывает сервис карт.",
        "url": "https://yandex.ru/maps/?" + urlencode({"text": query + " рядом с " + address}),
    }
