import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.campaigns import CampaignCreated, CampaignInput, create_campaign
from app.config import Settings
from app.db.models import Base, CampaignCreation, CampaignSnapshot, StreamDraft
from app.db.session import get_engine
from app.keitaro import Offer, OfferBinding, Stream
from app.publication import publish_draft
from app.stream_editor import offer_signature


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = Settings(database_path=Path(self.directory.name) / 'test.sqlite3')
        self.engine = get_engine(self.settings.database_path)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.data = CampaignInput(operation_id=uuid4(), name='Test', country='MX',
                                  offer_id=10, domain_id=1, group_id=1, traffic_source_id=1)
        self.result = CampaignCreated(id=1, name='Test', state='disabled', domain_id=1,
                                     group_id=1, traffic_source_id=1, geo_stream_id=2, offer_stream_id=3)

    def test_timeout_reserves_operation_and_retry_never_creates_duplicate(self):
        def timeout(data, settings, alias):
            # The reservation must already be durable before the remote POST.
            with Session(self.engine) as session:
                self.assertIsNotNone(session.get(CampaignCreation, str(data.operation_id)))
            raise HTTPException(504, 'Timeout')

        with patch('app.campaigns.validate_campaign'), patch('app.campaigns.perform_creation', side_effect=timeout) as create:
            with self.assertRaises(HTTPException) as first:
                create_campaign(self.data, self.settings)
            self.assertEqual(first.exception.status_code, 504)
            self.assertIn(self.data.operation_id.hex, first.exception.detail['alias'])
            with self.assertRaises(HTTPException) as retry:
                create_campaign(self.data, self.settings)
            self.assertEqual(retry.exception.status_code, 409)
            create.assert_called_once()

    def test_overlapping_request_is_blocked_before_second_remote_call(self):
        def finish(data, settings, alias):
            with self.assertRaises(HTTPException) as error:
                create_campaign(data, settings)
            self.assertEqual(error.exception.status_code, 409)
            return self.result

        with patch('app.campaigns.validate_campaign'), patch('app.campaigns.perform_creation', side_effect=finish) as create:
            self.assertEqual(create_campaign(self.data, self.settings), self.result)
            create.assert_called_once()

    def test_successful_retry_returns_original_result(self):
        with patch('app.campaigns.validate_campaign'), patch('app.campaigns.perform_creation', return_value=self.result) as create:
            self.assertEqual(create_campaign(self.data, self.settings), self.result)
            self.assertEqual(create_campaign(self.data, self.settings), self.result)
            create.assert_called_once()

    def test_operation_id_cannot_be_reused_with_different_payload(self):
        with patch('app.campaigns.validate_campaign'), patch('app.campaigns.perform_creation', return_value=self.result) as create:
            create_campaign(self.data, self.settings)
            with self.assertRaises(HTTPException) as error:
                create_campaign(self.data.model_copy(update={'name': 'Changed'}), self.settings)
            self.assertEqual(error.exception.status_code, 409)
            create.assert_called_once()

    def test_validation_failure_does_not_reserve_operation(self):
        with patch('app.campaigns.validate_campaign', side_effect=HTTPException(422, 'Invalid')):
            with self.assertRaises(HTTPException):
                create_campaign(self.data, self.settings)
        with Session(self.engine) as session:
            self.assertIsNone(session.get(CampaignCreation, str(self.data.operation_id)))

    def prepare_draft(self):
        activity = patch('app.publication.fetch_active_offer',
                         return_value=Offer(id=20, name='Offer', state='active'))
        activity.start()
        self.addCleanup(activity.stop)
        original = Stream(id=2, name='Offers', type='default', campaign_id=1,
                          offers=[OfferBinding(offer_id=10, share=100, state='active')])
        desired = original.model_copy(update={'offers': [OfferBinding(offer_id=20, share=100, state='active')]})
        with Session(self.engine) as session, session.begin():
            session.add(CampaignSnapshot(campaign_id=1, fetched_at=datetime.now(), streams=[original.model_dump()]))
            session.add(StreamDraft(campaign_id=1, stream_id=2, revision=1,
                                   baseline=offer_signature(original.offers),
                                   offers=[desired.offers[0].model_dump()]))
        return original, desired

    def test_publish_timeout_with_confirmed_remote_state_closes_draft(self):
        original, desired = self.prepare_draft()
        with patch('app.publication.read_target', side_effect=[original, desired]), patch('app.publication.replace_offers', side_effect=HTTPException(504, 'Timeout')):
            result = publish_draft(self.engine, self.settings, 1, 2, 1)
        self.assertFalse(result.dirty)
        self.assertEqual(result.revision, 2)
        with Session(self.engine) as session:
            self.assertIsNone(session.get(StreamDraft, (1, 2)).offers)
            self.assertEqual(session.get(CampaignSnapshot, 1).streams[0]['offers'], offer_signature(desired.offers))

    def test_publish_timeout_without_confirmation_keeps_draft(self):
        original, _ = self.prepare_draft()
        with patch('app.publication.read_target', return_value=original), patch('app.publication.replace_offers', side_effect=HTTPException(504, 'Timeout')):
            with self.assertRaises(HTTPException) as error:
                publish_draft(self.engine, self.settings, 1, 2, 1)
        self.assertEqual(error.exception.status_code, 504)
        with Session(self.engine) as session:
            self.assertIsNotNone(session.get(StreamDraft, (1, 2)).offers)

    def test_publish_failed_confirmation_read_keeps_draft(self):
        original, _ = self.prepare_draft()
        with patch('app.publication.read_target', side_effect=[original, HTTPException(504, 'Timeout')]), patch('app.publication.replace_offers'):
            with self.assertRaises(HTTPException):
                publish_draft(self.engine, self.settings, 1, 2, 1)
        with Session(self.engine) as session:
            self.assertIsNotNone(session.get(StreamDraft, (1, 2)).offers)
