import pytest
olm = pytest.importorskip("olm")  # noqa

import os
from collections import defaultdict
from tempfile import mkdtemp

from matrix_client.crypto.crypto_store import CryptoStore
from matrix_client.crypto.olm_device import OlmDevice


class TestCryptoStore(object):

    # Initialise a store and test some init code
    device_id = 'AUIETSRN'
    user_id = '@user:matrix.org'
    room_id = '!test:example.com'
    db_name = 'test.db'
    db_path = mkdtemp()
    store_conf = {
        'db_name': db_name,
        'db_path': db_path
    }
    store = CryptoStore(device_id, db_path=db_path, db_name=db_name)
    db_filepath = os.path.join(db_path, db_name)
    assert os.path.exists(db_filepath)
    store.close()
    store = CryptoStore(device_id, db_path=db_path, db_name='test.db')

    @pytest.fixture(autouse=True, scope='class')
    def cleanup(self):
        yield
        os.remove(self.db_filepath)

    @pytest.fixture()
    def account(self):
        account = self.store.get_olm_account()
        if account is None:
            account = olm.Account()
            self.store.save_olm_account(account)
        return account

    @pytest.fixture()
    def curve_key(self, account):
        return account.identity_keys['curve25519']

    @pytest.fixture()
    def device(self):
        return OlmDevice(None, self.user_id, self.device_id, store_conf=self.store_conf)

    def test_olm_account_persistence(self):
        account = olm.Account()
        identity_keys = account.identity_keys
        self.store.remove_olm_account()

        # Try to load inexisting account
        saved_account = self.store.get_olm_account()
        assert saved_account is None

        # Save and load
        self.store.save_olm_account(account)
        saved_account = self.store.get_olm_account()
        assert saved_account.identity_keys == identity_keys

        # Load the account from an OlmDevice
        device = OlmDevice(None, self.user_id, self.device_id, store_conf=self.store_conf)
        assert device.olm_account.identity_keys == account.identity_keys

    def test_olm_sessions_persistence(self, account, curve_key, device):
        session = olm.OutboundSession(account, curve_key, curve_key)
        sessions = defaultdict(list)

        self.store.load_olm_sessions(sessions)
        assert not sessions
        assert self.store.get_olm_sessions(curve_key) == []

        self.store.save_olm_session(curve_key, session)
        self.store.load_olm_sessions(sessions)
        assert sessions[curve_key][0].id == session.id

        saved_sessions = self.store.get_olm_sessions(curve_key)
        assert saved_sessions[0].id == session.id

        # Replace the session when its internal state has changed
        pickle = session.pickle()
        session.encrypt('test')
        self.store.save_olm_session(curve_key, session)
        saved_sessions = self.store.get_olm_sessions(curve_key)
        assert saved_sessions[0].pickle != pickle

        # Load sessions dynamically
        assert not device.olm_sessions
        with pytest.raises(AttributeError):
            device._olm_decrypt(None, curve_key)
        assert device.olm_sessions[curve_key][0].id == session.id

        device.olm_sessions.clear()
        device.device_keys[self.user_id][self.device_id] = {'curve25519': curve_key}
        device.olm_ensure_sessions({self.user_id: [self.device_id]})
        assert device.olm_sessions[curve_key][0].id == session.id

        # Test cascade deletion
        self.store.remove_olm_account()
        assert self.store.get_olm_sessions(curve_key) == []

    def test_megolm_inbound_persistence(self, curve_key, device):
        out_session = olm.OutboundGroupSession()
        session = olm.InboundGroupSession(out_session.session_key)
        sessions = defaultdict(lambda: defaultdict(dict))

        self.store.load_inbound_sessions(sessions)
        assert not sessions
        assert not self.store.get_inbound_session(self.room_id, curve_key)

        self.store.save_inbound_session(self.room_id, curve_key, session)
        self.store.load_inbound_sessions(sessions)
        assert sessions[self.room_id][curve_key][session.id].id == session.id

        saved_session = self.store.get_inbound_session(self.room_id, curve_key)
        assert saved_session.id == session.id

        assert not device.megolm_inbound_sessions
        created = device.megolm_add_inbound_session(
            self.room_id, curve_key, session.id, out_session.session_key)
        assert not created
        assert device.megolm_inbound_sessions[self.room_id][curve_key][session.id].id == \
            session.id

        device.megolm_inbound_sessions.clear()
        content = {
            'sender_key': curve_key,
            'session_id': session.id,
            'algorithm': device._megolm_algorithm,
            'device_id': ''
        }
        event = {
            'sender': '',
            'room_id': self.room_id,
            'content': content
        }
        with pytest.raises(KeyError):
            device.megolm_decrypt_event(event)
        assert device.megolm_inbound_sessions[self.room_id][curve_key][session.id].id == \
            session.id

        self.store.remove_olm_account()
        assert not self.store.get_inbound_session(self.room_id, curve_key)

    def test_load_all(self, account, curve_key):
        curve_key = account.identity_keys['curve25519']
        session = olm.OutboundSession(account, curve_key, curve_key)
        out_session = olm.OutboundGroupSession()
        in_session = olm.InboundGroupSession(out_session.session_key)

        self.store.save_inbound_session(self.room_id, curve_key, in_session)
        self.store.save_olm_session(curve_key, session)

        device = OlmDevice(
            None, self.user_id, self.device_id, store_conf=self.store_conf, load_all=True)

        assert session.id in {s.id for s in device.olm_sessions[curve_key]}
        saved_in_session = \
            device.megolm_inbound_sessions[self.room_id][curve_key][in_session.id]
        assert saved_in_session.id == in_session.id
