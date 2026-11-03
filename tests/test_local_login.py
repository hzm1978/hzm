from pathlib import Path
import tempfile
import unittest

from futures_replay.local_login import LocalLoginStore


class LocalLoginTests(unittest.TestCase):
    def test_encrypted_storage_is_separate_from_portable_state_and_can_be_cleared(self):
        # Storage contract test with an injected cipher; actual DPAPI is only
        # available under the Windows user's account, not this Linux runner.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'private'/'tianqin_login.dpapi'
            store = LocalLoginStore(path,protect=lambda x:bytes(v^137 for v in x),
                                    unprotect=lambda x:bytes(v^137 for v in x))
            store.save('offline-user','offline-only-secret')
            self.assertNotIn(b'offline-only-secret',path.read_bytes())
            self.assertEqual(store.load(),('offline-user','offline-only-secret'))
            self.assertFalse(list(path.parent.glob('*.tmp')))
            store.clear();self.assertIsNone(store.load())

    def test_failed_encryption_does_not_overwrite_a_saved_login_with_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'credentials';path.write_bytes(b'existing-encrypted-data')
            store = LocalLoginStore(path,protect=lambda x:x,unprotect=lambda x:x)
            with self.assertRaises(OSError): store.save('fake','fake-secret')
            self.assertEqual(path.read_bytes(),b'existing-encrypted-data')

    def test_another_users_or_corrupted_encrypted_file_is_not_used(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'credentials';path.write_bytes(b'unreadable')
            def decrypt(_): raise OSError('User-bound decryption failed')
            store = LocalLoginStore(path,protect=lambda x:x,unprotect=decrypt)
            self.assertIsNone(store.load())


if __name__ == '__main__':
    unittest.main()
