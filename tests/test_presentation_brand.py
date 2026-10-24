import hashlib
import json
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from korsik import brand_assets, profile_v04
from korsik.message_presentation import as_html, normalize, split_message, units
from korsik.models import Document, Identity, Material


def test_company_assets_match_preserved_source():
    data = brand_assets.manifest()
    assert len(data['assets']) == 16
    assert len(data['bindings']) == 25
    assert len(data['missing']) == 3
    source = json.loads((Path(__file__).parents[1] / 'examples/v04_materials.json').read_text(encoding='utf-8'))
    source_by_id = {item['id']: item for item in source['items']}
    for asset in data['assets']:
        assert hashlib.sha256((brand_assets.DIRECTORY / asset['file']).read_bytes()).hexdigest() == asset['sha256']
    for binding in data['bindings']:
        assert binding['label'] == 'Изображение ' + str(binding['image_number'])
        for field in ('label', 'cell', 'source_ref', 'mandatory'):
            assert binding[field] == source_by_id[binding['id']][field]


def test_install_is_idempotent_and_preserves_choices(runtime):
    with runtime.db.transaction() as session:
        profile_v04.install(session)
        material = session.get(Material, 'MAT-R004')
        material.url = 'https://example.org/hr-choice'
        first = brand_assets.install(session)
        assert len(first['installed']) == 24
        assert material.url == 'https://example.org/hr-choice'
        assert session.get(Material, 'MAT-R060').document_id is None
        second = brand_assets.install(session)
        assert second['installed'] == []
        docs = list(session.scalars(select(Document).where(Document.purpose == 'route_image')))
        assert len(docs) == 15
        assert all(d.status == 'ready' and d.published for d in docs)
        assert all((runtime.settings.data_dir / 'documents' / d.storage_name).exists() for d in docs)


def test_install_respects_hr_scope(runtime):
    with runtime.db.transaction() as session:
        profile_v04.install(session)
        actor = session.get(Identity, 'hr-a')
        assert brand_assets.install(session, actor)['installed'] == []


def test_html_escapes_input_and_formats_commands():
    formatted = as_html('Помощь\n- <script> & данные\n/menu — меню, /hr текст — HR\nhttps://example.org/menu')
    assert '<script>' not in formatted
    assert '• &lt;script&gt; &amp; данные' in formatted
    assert '<code>/menu</code> — меню\n<code>/hr</code>' in formatted
    assert 'https://example.org/menu' in formatted
    assert normalize('Заголовок\n\n- пункт') == 'Заголовок\n\n• пункт'


@pytest.mark.parametrize('text', ['слово ' * 3000, '😀' * 8000, '&<>' * 4000, 'я' * 10000, '**важно**\n' * 2000], ids=['words','emoji','escaped','unbroken','markdown'])
def test_split_retains_content_and_fits_wire_limit(text):
    parts = split_message(text)
    assert len(parts) > 1
    assert all(units(as_html(part)) <= 3500 for part in parts)
    assert re.sub(r'\s', '', ''.join(parts)) == re.sub(r'\s', '', normalize(text))


def test_company_assets_api_denies_manager(client):
    response = client.post('/api/admin/materials/company-images', headers={'Authorization': 'Bearer manager-key'})
    assert response.status_code == 403


def test_mascots_do_not_inflate_knowledge_dashboard(runtime, client):
    from conftest import auth

    with runtime.db.transaction() as session:
        profile_v04.install(session)
        brand_assets.install(session)
    assert client.get('/api/admin/overview', headers=auth('hr-a-key')).json()['document_count'] == 0


def test_max_wire_uses_safe_html(runtime):
    import httpx

    from korsik.max_client import MAXClient

    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={'message': {'body': {'mid': 'test'}}})

    with httpx.Client(base_url='https://platform-api2.max.ru', transport=httpx.MockTransport(handler)) as http:
        transport = MAXClient(runtime.settings, client=http)
        transport.send('123', {'text': 'Помощь\n/hr <текст>'})
        transport.send('123', {'text': '<старое>', 'presentation_format': 'legacy-plain'})
    assert bodies[0]['format'] == 'html'
    assert '<code>/hr</code> &lt;текст&gt;' in bodies[0]['text']
    assert bodies[1] == {'text': '<старое>'}


def test_worker_freezes_chunks_and_legacy_resume(runtime):
    from conftest import employee

    from korsik.calendar import now
    from korsik.models import Outbox
    from korsik.queue import send
    from korsik.worker import Worker

    employee(runtime)
    text = '😀 & слово ' * 1200
    with runtime.db.transaction() as session:
        item = send(session, 'i-e1', text, 'formatted-test', now(session))
        item_id = item.id
    assert Worker(runtime).deliver_one()
    with runtime.db.transaction() as session:
        item = session.get(Outbox, item_id)
        assert item.body['message_chunks'] == split_message(text)
        assert item.body['next_chunk'] == len(item.body['message_chunks'])
        assert item.status == 'sent'
        legacy = send(session, 'i-e1', 'а' * 7000, 'legacy-test', now(session))
        legacy.body = {**legacy.body, 'next_chunk': 1}
    before = len(runtime.transport.deliveries)
    assert Worker(runtime).deliver_one()
    sent = runtime.transport.deliveries[before:]
    assert len(sent) == 1
    assert sent[0]['body']['text'] == 'а' * 3500
    assert sent[0]['body']['presentation_format'] == 'legacy-plain'
