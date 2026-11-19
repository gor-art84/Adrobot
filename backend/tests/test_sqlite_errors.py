import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import httpx
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.campaigns import CampaignCreated
from app.config import Settings
from app.db.models import Base, CampaignCreation, CampaignSnapshot, StreamDraft
from app.db.session import get_engine
from app.main import app, get_settings


class SQLiteErrorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = Settings(database_path=Path(directory.name) / 'test.sqlite3')
        self.engine = get_engine(self.settings.database_path)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        app.dependency_overrides[get_settings] = lambda: self.settings
        self.addCleanup(app.dependency_overrides.clear)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)
        self.payload = dict(operation_id=str(uuid4()), name='Test', country='MX',
                            offer_id=20, domain_id=1, group_id=1, traffic_source_id=1)
        self.result = CampaignCreated(id=42, name='Test', state='disabled', domain_id=1,
                                     group_id=1, traffic_source_id=1, geo_stream_id=2, offer_stream_id=3)
        self.offers = [dict(offer_id=10, share=100, state='active', pinned=False, removed=False)]
        with Session(self.engine) as session, session.begin():
            session.add(CampaignSnapshot(campaign_id=1, fetched_at=datetime.now(), streams=[
                dict(id=2, name='Offers', type='default', campaign_id=1, offers=self.offers),
            ]))
            session.add(StreamDraft(campaign_id=1, stream_id=2, revision=5,
                                    baseline=self.offers, published_offers=self.offers, offers=self.offers))

    @contextmanager
    def fail_sql(self, fragment):
        def fail(connection, cursor, statement, parameters, context, executemany):
            if fragment in statement:
                raise OperationalError(statement, parameters, sqlite3.OperationalError('database locked INTERNAL_SECRET'))
        event.listen(self.engine, 'before_cursor_execute', fail)
        try:
            yield
        finally:
            event.remove(self.engine, 'before_cursor_execute', fail)

    @contextmanager
    def fail_commit(self, number=1):
        calls = 0
        def fail(connection):
            nonlocal calls
            calls += 1
            if calls == number:
                raise OperationalError('COMMIT', {}, sqlite3.OperationalError('disk full INTERNAL_SECRET'))
        event.listen(self.engine, 'commit', fail)
        try:
            yield
        finally:
            event.remove(self.engine, 'commit', fail)

    def assert_storage_error(self, response):
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn('SQLite', response.text)
        self.assertNotIn('INTERNAL_SECRET', response.text)
        self.assertNotIn('SELECT ', response.text)
        self.assertNotIn('UPDATE ', response.text)

    async def test_unavailable_database_returns_503_before_remote_creation(self):
        # SQLite cannot open a database inside a directory that does not exist.
        settings = self.settings.model_copy(update={'database_path': self.settings.database_path.parent / 'missing' / 'db.sqlite3'})
        engine = get_engine(settings.database_path)
        self.addCleanup(engine.dispose)
        app.dependency_overrides[get_settings] = lambda: settings
        with patch('app.campaigns.perform_creation') as create, self.assertLogs('app.campaigns', level='ERROR') as logs:
            response = await self.client.post('/api/campaigns', json=self.payload)
        self.assert_storage_error(response)
        self.assertIn('не отправлялся', response.json()['detail']['message'])
        self.assertNotIn('INTERNAL_SECRET', str(logs.output))
        create.assert_not_called()

    async def test_campaign_read_reservation_and_commit_failures_do_not_call_keitaro(self):
        cases = [('FROM campaign_creations', False), ('BEGIN IMMEDIATE', False),
                 ('INSERT INTO campaign_creations', False), ('', True)]
        for fragment, commit in cases:
            with self.subTest(fragment=fragment, commit=commit):
                failure = self.fail_commit() if commit else self.fail_sql(fragment)
                with patch('app.campaigns.validate_campaign'), patch('app.campaigns.perform_creation') as create, \
                     self.assertLogs('app.campaigns', level='ERROR') as logs, failure:
                    response = await self.client.post('/api/campaigns', json=self.payload)
                self.assert_storage_error(response)
                self.assertIn('не отправлялся', response.json()['detail']['message'])
                self.assertNotIn('INTERNAL_SECRET', str(logs.output))
                create.assert_not_called()
                with Session(self.engine) as session:
                    self.assertEqual(list(session.scalars(select(CampaignCreation))), [])

    async def test_result_save_failure_reports_created_id_and_retry_cannot_duplicate(self):
        for commit in (False, True):
            with self.subTest(commit=commit):
                self.payload['operation_id'] = str(uuid4())
                failure = self.fail_commit(number=2) if commit else self.fail_sql('UPDATE campaign_creations')
                with patch('app.campaigns.validate_campaign'), \
                     patch('app.campaigns.perform_creation', return_value=self.result) as create:
                    with self.assertLogs('app.campaigns', level='ERROR'), failure:
                        response = await self.client.post('/api/campaigns', json=self.payload)
                    self.assert_storage_error(response)
                    detail = response.json()['detail']
                    self.assertEqual(detail['campaign_id'], 42)
                    self.assertEqual(detail['alias'], 'adrobot-' + self.payload['operation_id'].replace('-', ''))
                    self.assertIn('создана в Keitaro', detail['message'])
                    self.assertIn('Не создавайте', detail['message'])
                    with Session(self.engine) as session:
                        reservation = session.get(CampaignCreation, self.payload['operation_id'])
                        self.assertIsNotNone(reservation)
                        self.assertIsNone(reservation.result)
                    retry = await self.client.post('/api/campaigns', json=self.payload)
                    self.assertEqual(retry.status_code, 409, retry.text)
                    create.assert_called_once()

    def draft_state(self):
        with Session(self.engine) as session:
            draft = session.get(StreamDraft, (1, 2))
            return (draft.revision, draft.offers, draft.baseline, draft.published_offers,
                    session.get(CampaignSnapshot, 1).streams)

    async def test_draft_errors_return_503_and_roll_back_changes(self):
        for fragment, commit in [('BEGIN IMMEDIATE', False), ('FROM campaign_snapshots', False),
                                 ('UPDATE stream_drafts', False), ('', True)]:
            with self.subTest(fragment=fragment, commit=commit):
                before = self.draft_state()
                failure = self.fail_commit() if commit else self.fail_sql(fragment)
                with self.assertLogs('app.main', level='ERROR') as logs, failure:
                    response = await self.client.post('/api/campaigns/1/streams/2/draft',
                                                     json=dict(action='pin', offer_id=10, revision=5))
                self.assert_storage_error(response)
                self.assertIn('Повторите', response.json()['detail'])
                self.assertNotIn('INTERNAL_SECRET', str(logs.output))
                self.assertEqual(self.draft_state(), before)
        # Both the DB transaction and the application lock must have been released.
        response = await self.client.post('/api/campaigns/1/streams/2/draft',
                                         json=dict(action='pin', offer_id=10, revision=5))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['revision'], 6)
        self.assertTrue(response.json()['offers'][0]['pinned'])

    async def test_database_failure_does_not_leave_a_new_draft(self):
        with Session(self.engine) as session, session.begin():
            session.delete(session.get(StreamDraft, (1, 2)))
        with self.fail_sql('INSERT INTO stream_drafts'), self.assertLogs('app.main', level='ERROR'):
            response = await self.client.post('/api/campaigns/1/streams/2/draft',
                                             json=dict(action='pin', offer_id=10, revision=0))
        self.assert_storage_error(response)
        with Session(self.engine) as session:
            self.assertIsNone(session.get(StreamDraft, (1, 2)))

    async def test_draft_domain_errors_keep_original_statuses(self):
        for stream_id, action, revision, status in [(999, 'pin', 5, 404), (2, 'pin', 99, 409),
                                                  (2, 'restore', 5, 422)]:
            with self.subTest(status=status), patch('app.main.fetch_active_offer'):
                response = await self.client.post(f'/api/campaigns/1/streams/{stream_id}/draft',
                                                 json=dict(action=action, offer_id=10, revision=revision))
                self.assertEqual(response.status_code, status, response.text)
    async def test_real_sqlite_write_lock_returns_503_for_both_operations(self):
        with self.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA busy_timeout=1')
        before = self.draft_state()
        blocker = sqlite3.connect(self.settings.database_path)
        try:
            blocker.execute('BEGIN IMMEDIATE')
            with patch('app.campaigns.validate_campaign'), patch('app.campaigns.perform_creation') as create, \
                 self.assertLogs('app.campaigns', level='ERROR'):
                response = await self.client.post('/api/campaigns', json=self.payload)
            self.assert_storage_error(response)
            create.assert_not_called()
            with self.assertLogs('app.main', level='ERROR'):
                response = await self.client.post('/api/campaigns/1/streams/2/draft',
                                                 json=dict(action='pin', offer_id=10, revision=5))
            self.assert_storage_error(response)
        finally:
            blocker.rollback()
            blocker.close()
        self.assertEqual(self.draft_state(), before)
        with Session(self.engine) as session:
            self.assertIsNone(session.get(CampaignCreation, self.payload['operation_id']))
