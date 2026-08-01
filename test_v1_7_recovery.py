import unittest
from unittest.mock import MagicMock, patch
import json
import os

from main import WatcherService
from config import Config
from state_store import StateStore
from journal import Journal
import docker.errors

class TestV17Recovery(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.config = Config()
        self.config.state_path = "test_recovery_state.json"
        self.config.journal_path = "test_recovery_journal.json"
        
        # Reset state
        if os.path.exists(self.config.state_path):
            os.remove(self.config.state_path)
        if os.path.exists(self.config.journal_path):
            os.remove(self.config.journal_path)
            
        with patch('docker.from_env', return_value=self.mock_client):
            self.service = WatcherService(self.config)

    def tearDown(self):
        if os.path.exists(self.config.state_path):
            os.remove(self.config.state_path)
        if os.path.exists(self.config.journal_path):
            os.remove(self.config.journal_path)

    def test_recovery_matching_ids(self):
        """Recovery mit übereinstimmenden IDs"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "backup_renamed", backup_container_id="orig_123", new_container_id="new_456")
        
        mock_backup = MagicMock()
        mock_backup.id = "orig_123"
        mock_backup.image.id = "img_1"
        mock_backup.name = "app_backup"
        
        mock_orig = MagicMock()
        mock_orig.id = "new_456"
        mock_orig.name = "app"
        
        def get_container(name):
            if name == "app_backup": return mock_backup
            if name == "app": return mock_orig
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        # It's in "backup_renamed", so it should restore the backup
        with patch.object(self.service.health, 'wait_for_health', return_value=True):
            self.service.startup_recovery()
            
        mock_orig.remove.assert_called_once_with(force=True)
        mock_backup.rename.assert_called_once_with("app")
        mock_backup.start.assert_called_once()
        
        # Transaction should be deleted because rollback succeeded and health check passed
        self.assertNotIn("app", self.service.state_store.get_transactions())

    def test_recovery_wrong_original_id(self):
        """Recovery mit falscher Original Container ID"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "replacement_created", backup_container_id="orig_123")
        
        # Make the backup ID mismatch
        mock_backup = MagicMock()
        mock_backup.id = "wrong_id_789"
        mock_backup.image.id = "img_1"
        mock_backup.name = "app_backup"
        
        def get_container(name):
            if name == "app_backup": return mock_backup
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        self.service.startup_recovery()
        
        # Transaction should be retained
        self.assertIn("app", self.service.state_store.get_transactions())
        mock_backup.rename.assert_not_called()

    def test_recovery_wrong_backup_id(self):
        """Recovery mit falscher Backup Container ID"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "prepared", backup_container_id="orig_123")
        
        mock_backup = MagicMock()
        mock_backup.id = "different_backup_id"
        mock_backup.image.id = "img_1"
        
        def get_container(name):
            if name == "app_backup": return mock_backup
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        self.service.startup_recovery()
        
        self.assertIn("app", self.service.state_store.get_transactions())

    def test_recovery_missing_backup(self):
        """Recovery mit fehlendem Backup"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "backup_renamed", backup_container_id="orig_123")
        
        def get_container(name):
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        self.service.startup_recovery()
        
        # Should retain transaction
        self.assertIn("app", self.service.state_store.get_transactions())

    def test_recovery_failed_backup_start(self):
        """Recovery mit fehlgeschlagenem Backup Start"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "backup_renamed", backup_container_id="orig_123")
        
        mock_backup = MagicMock()
        mock_backup.id = "orig_123"
        mock_backup.image.id = "img_1"
        mock_backup.start.side_effect = Exception("Start failed")
        
        def get_container(name):
            if name == "app_backup": return mock_backup
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        self.service.startup_recovery()
        self.assertIn("app", self.service.state_store.get_transactions())

    def test_recovery_failed_backup_health(self):
        """Recovery mit fehlgeschlagenem Backup Health Check"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "backup_renamed", backup_container_id="orig_123")
        
        mock_backup = MagicMock()
        mock_backup.id = "orig_123"
        mock_backup.image.id = "img_1"
        
        def get_container(name):
            if name == "app_backup": return mock_backup
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        with patch.object(self.service.health, 'wait_for_health', return_value=False):
            self.service.startup_recovery()
            
        # Transaction retained
        self.assertEqual(self.service.state_store.get_transactions()["app"]["phase"], "rollback_failed")

    def test_transaction_retained_on_failed_rollback(self):
        """Transaktion bleibt bei fehlgeschlagenem Rollback erhalten"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "replacement_verified", backup_container_id="orig_123")
        
        def get_container(name):
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        # During process_container, perform_rollback would be called
        success, _ = self.service.perform_rollback("app", "img_1")
        
        self.assertFalse(success)
        self.assertEqual(self.service.state_store.get_transactions()["app"]["phase"], "rollback_failed")

    def test_rollback_despite_statestore_error(self):
        """Rollback physisch ausgeführt, auch wenn rollback_started Speichern fehlschlägt"""
        from exceptions import StateStoreError
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "replacement_verified", backup_container_id="orig_123")
        
        mock_backup = MagicMock()
        mock_backup.id = "orig_123"
        mock_backup.image.id = "img_1"
        
        def get_container(name):
            if name == "app_backup": return mock_backup
            raise docker.errors.NotFound("Not found")
            
        self.mock_client.containers.get.side_effect = get_container
        
        # mock update_transaction to fail on 'rollback_started'
        original_update = self.service.state_store.update_transaction
        def mock_update(name, phase, **kwargs):
            if phase == "rollback_started":
                raise StateStoreError("Disk full")
            original_update(name, phase, **kwargs)
            
        with patch.object(self.service.state_store, 'update_transaction', side_effect=mock_update):
            with patch.object(self.service.health, 'wait_for_health', return_value=True):
                success, _ = self.service.perform_rollback("app", "img_1")
                
        # the rollback still works!
        self.assertTrue(success)
        mock_backup.rename.assert_called_with("app")
        mock_backup.start.assert_called_once()

    def test_transaction_deleted_only_after_verification(self):
        """Transaktion wird erst nach erfolgreicher Prüfung gelöscht"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "replacement_started", backup_container_id="orig_123", new_container_id="new_456")
        
        mock_backup = MagicMock()
        mock_backup.id = "orig_123"
        mock_backup.image.id = "img_1"
        mock_orig = MagicMock()
        mock_orig.id = "new_456"
        
        def get_container(name):
            if name == "app_backup": return mock_backup
            if name == "app": return mock_orig
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        with patch.object(self.service.health, 'wait_for_health', return_value=True):
            self.service.startup_recovery()
            
        self.assertNotIn("app", self.service.state_store.get_transactions())

    def test_transaction_retained_on_remove_backup_false(self):
        """Neuer Container ist gesund, remove_backup liefert False, Transaktion bleibt replacement_verified erhalten"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "replacement_started", backup_container_id="orig_123", new_container_id="new_456")
        
        mock_backup = MagicMock()
        mock_backup.id = "orig_123"
        mock_backup.image.id = "img_1"
        mock_orig = MagicMock()
        mock_orig.id = "new_456"
        
        def get_container(name):
            if name == "app_backup": return mock_backup
            if name == "app": return mock_orig
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        with patch.object(self.service.health, 'wait_for_health', return_value=True):
            with patch.object(self.service.docker, 'remove_backup', return_value=False):
                self.service.startup_recovery()
            
        self.assertIn("app", self.service.state_store.get_transactions())
        self.assertEqual(self.service.state_store.get_transactions()["app"]["phase"], "replacement_verified")

    def test_foreign_backup_ignored(self):
        """Fremder _backup Container bleibt unangetastet"""
        mock_c1 = MagicMock()
        mock_c1.name = "foreign_backup"
        self.mock_client.containers.list.return_value = [mock_c1]
        
        self.service.startup_recovery()
        mock_c1.remove.assert_not_called()
        mock_c1.rename.assert_not_called()

    def test_journal_json_decode_error(self):
        """JSONDecodeError im Journal erzeugt Sicherungsdatei"""
        with open(self.config.journal_path, 'w') as f:
            f.write("{invalid_json:")
            
        # Initialize should trigger _load and backup
        j = Journal(True, self.config.journal_path)
        
        # Check if corrupted file was created
        found = False
        for f in os.listdir("."):
            if f.startswith("test_recovery_journal.json.corrupted_"):
                found = True
                os.remove(f)
                break
        self.assertTrue(found)

    def test_schedule_time_no_jitter(self):
        """SCHEDULE_TIME enthält keinen Jitter"""
        self.config.schedule_time = "14:00"
        
        # Test it uses datetime exact logic
        # We can't easily mock datetime.now() perfectly, but we can verify it doesn't call random
        with patch('random.uniform') as mock_uniform:
            # We already removed random import entirely from main.py, so it shouldn't be there
            import sys
            if 'main' in sys.modules:
                del sys.modules['main']
            import main
            
            self.assertFalse(hasattr(main, 'random'))


    def test_backup_id_none_original_id_matches(self):
        """backup_container_id ist None, original_container_id stimmt"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "backup_renamed", new_container_id="new_456")
        
        # Simuliere Zustand vor dem Start des Replacements
        self.service.state_store.get_transactions()["app"]["backup_container_id"] = None
        
        mock_backup = MagicMock()
        mock_backup.id = "orig_123"
        mock_backup.image.id = "img_1"
        
        mock_orig = MagicMock()
        mock_orig.id = "new_456"
        
        def get_container(name):
            if name == "app_backup": return mock_backup
            if name == "app": return mock_orig
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        with patch.object(self.service.health, 'wait_for_health', return_value=True):
            self.service.startup_recovery()
            
        mock_orig.remove.assert_called_once()
        mock_backup.rename.assert_called_once_with("app")

    def test_backup_id_none_original_id_mismatch(self):
        """backup_container_id ist None, original_container_id stimmt nicht"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "backup_renamed")
        
        mock_backup = MagicMock()
        mock_backup.id = "wrong_123"
        mock_backup.image.id = "img_1"
        mock_backup.name = "app_backup"
        
        def get_container(name):
            if name == "app_backup": return mock_backup
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        self.service.startup_recovery()
        
        self.assertIn("app", self.service.state_store.get_transactions())
        mock_backup.rename.assert_not_called()

    def test_backup_image_id_mismatch(self):
        """Backup Image ID stimmt nicht"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "backup_renamed", backup_container_id="orig_123")
        
        mock_backup = MagicMock()
        mock_backup.id = "orig_123"
        mock_backup.image.id = "img_wrong"
        
        def get_container(name):
            if name == "app_backup": return mock_backup
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        self.service.startup_recovery()
        
        self.assertIn("app", self.service.state_store.get_transactions())
        mock_backup.rename.assert_not_called()

    def test_main_container_matches_new_id(self):
        """Regulärer Container entspricht new_container_id"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "replacement_created", backup_container_id="orig_123", new_container_id="new_456")
        
        mock_backup = MagicMock()
        mock_backup.id = "orig_123"
        mock_backup.image.id = "img_1"
        
        mock_orig = MagicMock()
        mock_orig.id = "new_456"
        
        def get_container(name):
            if name == "app_backup": return mock_backup
            if name == "app": return mock_orig
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        with patch.object(self.service.health, 'wait_for_health', return_value=True):
            self.service.startup_recovery()
            
        # success, transaction removed
        self.assertNotIn("app", self.service.state_store.get_transactions())

    def test_main_container_mismatch_new_id(self):
        """Regulärer Container entspricht new_container_id nicht"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "replacement_created", backup_container_id="orig_123", new_container_id="new_456")
        
        mock_backup = MagicMock()
        mock_backup.id = "orig_123"
        mock_backup.image.id = "img_1"
        
        mock_orig = MagicMock()
        mock_orig.id = "wrong_456"
        
        def get_container(name):
            if name == "app_backup": return mock_backup
            if name == "app": return mock_orig
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        self.service.startup_recovery()
        
        # Transaction should be retained
        self.assertIn("app", self.service.state_store.get_transactions())
        mock_backup.rename.assert_not_called()

    def test_prepared_phase_missing_backup_correct_original(self):
        """Prepared Phase mit fehlendem Backup und korrektem Original"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        # phase is "prepared" implicitly
        
        mock_orig = MagicMock()
        mock_orig.id = "orig_123"
        mock_orig.image.id = "img_1"
        
        def get_container(name):
            if name == "app": return mock_orig
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        with patch.object(self.service.health, 'wait_for_health', return_value=True):
            self.service.startup_recovery()
        
        # Transaction should be removed because original is correct and we didn't touch it
        self.assertNotIn("app", self.service.state_store.get_transactions())

    def test_prepared_phase_missing_backup_foreign_original(self):
        """Prepared Phase mit fehlendem Backup und fremdem Original"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        
        mock_orig = MagicMock()
        mock_orig.id = "different_123"
        mock_orig.image.id = "img_1"
        
        def get_container(name):
            if name == "app": return mock_orig
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container
        
        self.service.startup_recovery()
        
        # Transaction retained
        self.assertIn("app", self.service.state_store.get_transactions())

    def test_recovery_without_backup_after_successful_update(self):
        """Startup Recovery ohne Backup nach erfolgreichem Update"""
        self.service.state_store.start_transaction("app", "orig_123", "img_1")
        self.service.state_store.update_transaction("app", "replacement_verified", new_container_id="new_456", new_image_id="img_2")
        
        mock_orig = MagicMock()
        mock_orig.id = "new_456"
        mock_orig.image.id = "img_2"
        mock_orig.name = "app"
        
        def get_container(name):
            if name == "app": return mock_orig
            raise docker.errors.NotFound("Not found")
            
        self.mock_client.containers.get.side_effect = get_container
        
        with patch.object(self.service.health, 'wait_for_health', return_value=True):
            self.service.startup_recovery()
            
        self.assertNotIn("app", self.service.state_store.get_transactions())

if __name__ == '__main__':
    unittest.main()
