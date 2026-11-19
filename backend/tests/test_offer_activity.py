import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.campaigns import CampaignCreated, CampaignOptions, Reference
from app.config import Settings
from app.db.models import Base, CampaignCreation, CampaignSnapshot, StreamDraft
from app.db.session import get_engine
from app.main import app, get_settings
from app.keitaro import OfferBinding, Stream


class OfferActivityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = Settings(database_path=Path(directory.name) / 'test.sqlite3',
                                 keitaro_api_key='test-key')
        self.engine = get_engine(self.settings.database_path)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        app.dependency_overrides[get_settings] = lambda: self.settings
        self.addCleanup(app.dependency_overrides.clear)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.payload = dict(operation_id=str(uuid4()), name='Test', country='MX',
                            offer_id=20, domain_id=1, group_id=1, traffic_source_id=1)
        reference = Reference(id=1, name='Test')
        self.options = CampaignOptions(domains=[reference], groups=[reference],
                                       traffic_sources=[reference])
        self.result = CampaignCreated(id=1, name='Test', state='disabled', domain_id=1,
                                     group_id=1, traffic_source_id=1, geo_stream_id=2, offer_stream_id=3)
        self.original_offers = [dict(offer_id=10, share=100, state='active')]
        with Session(self.engine) as session, session.begin():
            session.add(CampaignSnapshot(campaign_id=1, fetched_at=datetime.now(), streams=[
                dict(id=2, name='Offers', type='default', campaign_id=1,
                     routing_schema='landings', offers=self.original_offers),
            ]))

    def offer_response(self, state='active', **overrides):
        return httpx.Response(200, json=dict(id=20, name='Offer 20', state=state, **overrides))

    def assert_no_creation(self):
        with Session(self.engine) as session:
            self.assertEqual(list(session.scalars(select(CampaignCreation))), [])

    def assert_no_draft(self):
        with Session(self.engine) as session:
            self.assertIsNone(session.get(StreamDraft, (1, 2)))
            self.assertEqual(session.get(CampaignSnapshot, 1).streams[0]['offers'], self.original_offers)

    async def test_active_offer_allows_campaign_creation(self):
        with patch('app.campaigns.load_campaign_options', return_value=self.options), \
             patch('app.keitaro.httpx.request', return_value=self.offer_response()) as request, \
             patch('app.campaigns.perform_creation', return_value=self.result) as create:
            response = await self.client.post('/api/campaigns', json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        create.assert_called_once()
        self.assertEqual(request.call_args.args[:2],
                         ('GET', f'{str(self.settings.keitaro_base_url).rstrip("/")}/admin_api/v1/offers/20'))

    async def test_inactive_offer_blocks_campaign_before_remote_creation(self):
        for state in ('disabled', 'deleted', 'archived', 'unknown', ''):
            with self.subTest(state=state), \
                 patch('app.campaigns.load_campaign_options', return_value=self.options), \
                 patch('app.keitaro.httpx.request', return_value=self.offer_response(state)), \
                 patch('app.campaigns.perform_creation') as create:
                response = await self.client.post('/api/campaigns', json=self.payload)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn('неактивен', response.json()['detail'])
                create.assert_not_called()
                self.assert_no_creation()

    async def test_active_offer_is_added_and_weights_are_distributed(self):
        with patch('app.keitaro.httpx.request', return_value=self.offer_response()):
            response = await self.client.post('/api/campaigns/1/streams/2/draft',
                                        json=dict(action='add', offer_id=20, revision=0))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['dirty'])
        with Session(self.engine) as session:
            draft = session.get(StreamDraft, (1, 2))
            self.assertEqual(draft.revision, 1)
            self.assertEqual([(row['offer_id'], row['share']) for row in draft.offers], [(10, 50), (20, 50)])
            self.assertEqual(draft.offers[1]['name'], 'Offer 20')
            self.assertEqual(draft.baseline, self.original_offers)

    async def test_inactive_offer_does_not_create_draft(self):
        for state in ('disabled', 'deleted', 'archived', 'unknown', ''):
            with self.subTest(state=state), patch('app.keitaro.httpx.request', return_value=self.offer_response(state)):
                response = await self.client.post('/api/campaigns/1/streams/2/draft',
                                            json=dict(action='add', offer_id=20, revision=0))
                self.assertEqual(response.status_code, 422, response.text)
                self.assert_no_draft()

    async def test_rejected_add_preserves_existing_draft_revision_and_weights(self):
        with Session(self.engine) as session, session.begin():
            session.add(StreamDraft(campaign_id=1, stream_id=2, revision=5,
                                    baseline=self.original_offers, offers=self.original_offers))
        with patch('app.keitaro.httpx.request', return_value=self.offer_response('deleted')):
            response = await self.client.post('/api/campaigns/1/streams/2/draft',
                                        json=dict(action='add', offer_id=20, revision=5))
        self.assertEqual(response.status_code, 422)
        with Session(self.engine) as session:
            draft = session.get(StreamDraft, (1, 2))
            self.assertEqual(draft.revision, 5)
            self.assertEqual(draft.offers, self.original_offers)
            self.assertEqual(draft.baseline, self.original_offers)

    async def test_malformed_or_missing_state_fails_closed_in_both_endpoints(self):
        for body in (dict(id=20, name='Offer'), dict(id=20, name='Offer', state=None),
                     dict(id=20, name='Offer', state=1), dict(id=99, name='Wrong', state='active')):
            with self.subTest(body=body), \
                 patch('app.campaigns.load_campaign_options', return_value=self.options), \
                 patch('app.keitaro.httpx.request', return_value=httpx.Response(200, json=body)), \
                 patch('app.campaigns.perform_creation') as create:
                self.assertEqual((await self.client.post('/api/campaigns', json=self.payload)).status_code, 502)
                response = await self.client.post('/api/campaigns/1/streams/2/draft',
                                            json=dict(action='add', offer_id=20, revision=0))
                self.assertEqual(response.status_code, 502)
                create.assert_not_called()
                self.assert_no_creation()
                self.assert_no_draft()

    async def test_not_found_and_upstream_failures_preserve_local_state(self):
        for status, expected in ((404, 404), (503, 502)):
            with self.subTest(status=status), \
                 patch('app.campaigns.load_campaign_options', return_value=self.options), \
                 patch('app.keitaro.httpx.request', return_value=httpx.Response(status)), \
                 patch('app.campaigns.perform_creation') as create:
                self.assertEqual((await self.client.post('/api/campaigns', json=self.payload)).status_code, expected)
                response = await self.client.post('/api/campaigns/1/streams/2/draft',
                                            json=dict(action='add', offer_id=20, revision=0))
                self.assertEqual(response.status_code, expected)
                create.assert_not_called()
                self.assert_no_creation()
                self.assert_no_draft()

    async def test_timeout_while_checking_offer_leaves_no_local_changes(self):
        with patch('app.campaigns.load_campaign_options', return_value=self.options), \
             patch('app.keitaro.httpx.request', side_effect=httpx.ReadTimeout('Timeout')), \
             patch('app.campaigns.perform_creation') as create:
            self.assertEqual((await self.client.post('/api/campaigns', json=self.payload)).status_code, 504)
            response = await self.client.post('/api/campaigns/1/streams/2/draft',
                                             json=dict(action='add', offer_id=20, revision=0))
            self.assertEqual(response.status_code, 504)
            create.assert_not_called()
            self.assert_no_creation()
            self.assert_no_draft()

    async def test_same_operation_can_be_retried_after_offer_becomes_active(self):
        with patch('app.campaigns.load_campaign_options', return_value=self.options), \
             patch('app.keitaro.httpx.request', side_effect=[self.offer_response('deleted'), self.offer_response()]), \
             patch('app.campaigns.perform_creation', return_value=self.result) as create:
            first = await self.client.post('/api/campaigns', json=self.payload)
            self.assertEqual(first.status_code, 422)
            self.assert_no_creation()
            second = await self.client.post('/api/campaigns', json=self.payload)
            self.assertEqual(second.status_code, 201, second.text)
            create.assert_called_once()
    def prepare_restore(self):
        offers = [*self.original_offers,
                  dict(offer_id=20, share=0, state='active', removed=True, pinned=False, name='Offer 20')]
        with Session(self.engine) as session, session.begin():
            draft = session.get(StreamDraft, (1, 2))
            if draft is None:
                draft = StreamDraft(campaign_id=1, stream_id=2)
                session.add(draft)
            draft.revision = 5
            draft.baseline = self.original_offers
            draft.offers = offers
            draft.published_offers = offers
        return self.local_state()

    def local_state(self):
        with Session(self.engine) as session:
            draft = session.get(StreamDraft, (1, 2))
            snapshot = session.get(CampaignSnapshot, 1)
            return dict(revision=draft.revision, offers=draft.offers,
                        baseline=draft.baseline, published=draft.published_offers,
                        streams=snapshot.streams, fetched_at=snapshot.fetched_at)

    async def test_bring_back_active_offer_restores_and_redistributes(self):
        self.prepare_restore()
        with patch('app.keitaro.httpx.request', return_value=self.offer_response()) as request:
            response = await self.client.post('/api/campaigns/1/streams/2/draft',
                                             json=dict(action='restore', offer_id=20, revision=5))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(request.call_args.args[0], 'GET')
        result = response.json()
        self.assertEqual(result['revision'], 6)
        self.assertTrue(result['dirty'])
        self.assertEqual([row['share'] for row in result['offers']], [50, 50])
        self.assertFalse(result['offers'][1]['removed'])

    async def test_bring_back_rejects_inactive_and_invalid_offers_without_changing_draft(self):
        cases = [(self.offer_response(state), 422) for state in ('disabled', 'deleted', 'archived', 'unknown')]
        cases += [(httpx.Response(200, json=dict(id=20, name='Missing state')), 502),
                  (httpx.Response(200, json=dict(id=99, name='Wrong ID', state='active')), 502),
                  (httpx.Response(404), 404), (httpx.Response(503), 502)]
        for remote, status in cases:
            with self.subTest(response=remote.content):
                before = self.prepare_restore()
                with patch('app.keitaro.httpx.request', return_value=remote):
                    response = await self.client.post('/api/campaigns/1/streams/2/draft',
                                                     json=dict(action='restore', offer_id=20, revision=5))
                self.assertEqual(response.status_code, status, response.text)
                self.assertEqual(self.local_state(), before)

    async def test_bring_back_timeout_keeps_draft(self):
        before = self.prepare_restore()
        with patch('app.keitaro.httpx.request', side_effect=httpx.ReadTimeout('Timeout')):
            response = await self.client.post('/api/campaigns/1/streams/2/draft',
                                             json=dict(action='restore', offer_id=20, revision=5))
        self.assertEqual(response.status_code, 504)
        self.assertEqual(self.local_state(), before)

    def prepare_publication(self, offers=None):
        if offers is None:
            offers = [dict(offer_id=20, share=100, state='active'),
                      dict(offer_id=10, share=0, state='active', removed=True)]
        with Session(self.engine) as session, session.begin():
            draft = session.get(StreamDraft, (1, 2))
            if draft is None:
                draft = StreamDraft(campaign_id=1, stream_id=2)
                session.add(draft)
            draft.revision = 5
            draft.baseline = self.original_offers
            draft.offers = offers
            draft.published_offers = self.original_offers
        original = Stream(id=2, name='Offers', type='default', campaign_id=1,
                          offers=self.original_offers)
        desired = original.model_copy(update={'offers': [
            # Remote bindings do not include local archive/pinning metadata.
            OfferBinding.model_validate(row)
            for row in offers if not row.get('removed', False)
        ]})
        return original, desired

    async def test_publish_active_offer_checks_catalog_and_excludes_removed_offers(self):
        original, desired = self.prepare_publication()
        with patch('app.publication.read_target', side_effect=[original, desired]), \
             patch('app.keitaro.httpx.request', return_value=self.offer_response()) as request, \
             patch('app.publication.replace_offers') as write:
            response = await self.client.post('/api/campaigns/1/streams/2/push', json=dict(revision=5))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['dirty'])
        self.assertEqual(response.json()['revision'], 6)
        request.assert_called_once()
        self.assertTrue(request.call_args.args[1].endswith('/offers/20'))
        write.assert_called_once_with(2, desired.offers, self.settings)
        self.assertIsNone(self.local_state()['offers'])

    async def test_publish_rejects_inactive_or_unverifiable_offers_before_write(self):
        cases = [(self.offer_response(state), 422) for state in ('disabled', 'deleted', 'archived', 'unknown')]
        cases += [(httpx.Response(200, json=dict(id=20, name='Missing state')), 502),
                  (httpx.Response(200, json=dict(id=20, name='Null state', state=None)), 502),
                  (httpx.Response(200, json=dict(id=99, name='Wrong ID', state='active')), 502),
                  (httpx.Response(404), 404), (httpx.Response(503), 502)]
        for remote, status in cases:
            with self.subTest(response=remote.content):
                original, _ = self.prepare_publication()
                before = self.local_state()
                with patch('app.publication.read_target', return_value=original), \
                     patch('app.keitaro.httpx.request', return_value=remote), \
                     patch('app.publication.replace_offers') as write:
                    response = await self.client.post('/api/campaigns/1/streams/2/push', json=dict(revision=5))
                self.assertEqual(response.status_code, status, response.text)
                write.assert_not_called()
                self.assertEqual(self.local_state(), before)

    async def test_publish_timeout_checking_activity_keeps_draft_and_snapshot(self):
        original, _ = self.prepare_publication()
        before = self.local_state()
        with patch('app.publication.read_target', return_value=original), \
             patch('app.keitaro.httpx.request', side_effect=httpx.ReadTimeout('Timeout')), \
             patch('app.publication.replace_offers') as write:
            response = await self.client.post('/api/campaigns/1/streams/2/push', json=dict(revision=5))
        self.assertEqual(response.status_code, 504)
        write.assert_not_called()
        self.assertEqual(self.local_state(), before)

    async def test_publish_checks_all_retained_offers_including_disabled_bindings(self):
        original, _ = self.prepare_publication([
            dict(offer_id=20, share=100, state='active'),
            dict(offer_id=30, share=0, state='disabled'),
        ])
        before = self.local_state()
        with patch('app.publication.read_target', return_value=original), \
             patch('app.keitaro.httpx.request', side_effect=[
                 self.offer_response(),
                 httpx.Response(200, json=dict(id=30, name='Archived', state='deleted')),
             ]) as request, patch('app.publication.replace_offers') as write:
            response = await self.client.post('/api/campaigns/1/streams/2/push', json=dict(revision=5))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(request.call_count, 2)
        self.assertTrue(request.call_args.args[1].endswith('/offers/30'))
        write.assert_not_called()
        self.assertEqual(self.local_state(), before)

    async def test_publish_checks_activity_even_when_remote_already_matches(self):
        for active in (False, True):
            with self.subTest(active=active):
                _, desired = self.prepare_publication()
                before = self.local_state()
                with patch('app.publication.read_target', return_value=desired), \
                     patch('app.keitaro.httpx.request', return_value=self.offer_response('active' if active else 'deleted')), \
                     patch('app.publication.replace_offers') as write:
                    response = await self.client.post('/api/campaigns/1/streams/2/push', json=dict(revision=5))
                write.assert_not_called()
                self.assertEqual(response.status_code, 200 if active else 422, response.text)
                if active:
                    self.assertIsNone(self.local_state()['offers'])
                else:
                    self.assertEqual(self.local_state(), before)

    async def test_publish_can_remove_all_archived_offers_without_catalog_checks(self):
        original, desired = self.prepare_publication([
            dict(offer_id=10, share=0, state='active', removed=True),
        ])
        with patch('app.publication.read_target', side_effect=[original, desired]), \
             patch('app.keitaro.httpx.request') as request, \
             patch('app.publication.replace_offers') as write:
            response = await self.client.post('/api/campaigns/1/streams/2/push', json=dict(revision=5))
        self.assertEqual(response.status_code, 200, response.text)
        request.assert_not_called()
        write.assert_called_once_with(2, [], self.settings)
    async def test_bring_back_checks_offer_from_published_archive_without_open_draft(self):
        for active in (False, True):
            with self.subTest(active=active):
                self.prepare_restore()
                with Session(self.engine) as session, session.begin():
                    session.get(StreamDraft, (1, 2)).offers = None
                before = self.local_state()
                with patch('app.keitaro.httpx.request', return_value=self.offer_response('active' if active else 'deleted')):
                    response = await self.client.post('/api/campaigns/1/streams/2/draft',
                                                     json=dict(action='restore', offer_id=20, revision=5))
                self.assertEqual(response.status_code, 200 if active else 422, response.text)
                if active:
                    self.assertTrue(response.json()['dirty'])
                    self.assertFalse(response.json()['offers'][1]['removed'])
                else:
                    self.assertEqual(self.local_state(), before)
