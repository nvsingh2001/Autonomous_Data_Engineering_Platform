import os
import unittest
from unittest.mock import MagicMock, patch

import config
from utils.storage import LocalBackend, S3Backend, get_storage_backend


class TestLocalBackend(unittest.TestCase):
    def test_never_touches_boto3(self):
        backend = LocalBackend()
        with patch("boto3.client") as mock_client:
            self.assertIsNone(backend.upload_file("x", "k"))
            self.assertFalse(backend.download_file("k", "x"))
            self.assertIsNone(backend.delete("k"))
            self.assertIsNone(backend.sync_dir_up("d", "p"))
            self.assertIsNone(backend.sync_dir_down("p", "d"))
        mock_client.assert_not_called()


class TestGetStorageBackend(unittest.TestCase):
    def test_defaults_to_local(self):
        with patch.object(config, "STORAGE_BACKEND", "local"):
            self.assertIsInstance(get_storage_backend(), LocalBackend)

    def test_s3_when_configured(self):
        with patch.object(config, "STORAGE_BACKEND", "s3"), patch.object(
            config, "S3_BUCKET", "my-bucket"
        ), patch.object(config, "S3_REGION", "us-east-1"), patch("boto3.client"):
            self.assertIsInstance(get_storage_backend(), S3Backend)


class TestS3Backend(unittest.TestCase):
    def setUp(self):
        patcher = patch("boto3.client")
        self.mock_boto_client = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_client = MagicMock()
        self.mock_boto_client.return_value = self.mock_client
        self.backend = S3Backend(bucket="my-bucket", region="us-east-1")

    def test_upload_file(self):
        self.backend.upload_file("/local/path.db", "warehouse/warehouse.db")
        self.mock_client.upload_file.assert_called_once_with(
            "/local/path.db", "my-bucket", "warehouse/warehouse.db"
        )

    def test_upload_file_with_prefix(self):
        backend = S3Backend(bucket="my-bucket", region="us-east-1", prefix="dev")
        backend.upload_file("/local/path.db", "warehouse/warehouse.db")
        self.mock_client.upload_file.assert_called_once_with(
            "/local/path.db", "my-bucket", "dev/warehouse/warehouse.db"
        )

    def test_upload_file_swallows_errors(self):
        self.mock_client.upload_file.side_effect = RuntimeError("network down")
        self.backend.upload_file("/local/path.db", "warehouse/warehouse.db")  # must not raise

    def test_download_file_success(self):
        result = self.backend.download_file("warehouse/warehouse.db", "/local/path.db")
        self.mock_client.download_file.assert_called_once_with(
            "my-bucket", "warehouse/warehouse.db", "/local/path.db"
        )
        self.assertTrue(result)

    def test_download_file_not_found_returns_false(self):
        from botocore.exceptions import ClientError

        self.mock_client.download_file.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "GetObject"
        )
        result = self.backend.download_file("missing/key", "/local/path.db")
        self.assertFalse(result)

    def test_delete(self):
        self.backend.delete("warehouse/warehouse.db")
        self.mock_client.delete_object.assert_called_once_with(
            Bucket="my-bucket", Key="warehouse/warehouse.db"
        )


class TestS3BackendDirSync(unittest.TestCase):
    def setUp(self):
        patcher = patch("boto3.client")
        self.mock_boto_client = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_client = MagicMock()
        self.mock_boto_client.return_value = self.mock_client
        self.backend = S3Backend(bucket="my-bucket", region="us-east-1")

    def test_sync_dir_up_skips_excluded_and_uploads_rest(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "charts"))
            with open(os.path.join(tmp, "kpi_report.md"), "w") as f:
                f.write("x")
            with open(os.path.join(tmp, "execution.log"), "w") as f:
                f.write("x")
            with open(os.path.join(tmp, "charts", "a.png"), "w") as f:
                f.write("x")

            self.backend.sync_dir_up(tmp, "reports", exclude={"execution.log"})

            uploaded_keys = {c.args[2] for c in self.mock_client.upload_file.call_args_list}
            self.assertEqual(
                uploaded_keys, {"reports/kpi_report.md", "reports/charts/a.png"}
            )

    def test_sync_dir_down_noop_when_local_dir_has_files(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "existing.csv"), "w") as f:
                f.write("x")
            self.backend.sync_dir_down("data", tmp)
        self.mock_client.list_objects_v2.assert_not_called()

    def test_sync_dir_down_downloads_when_local_dir_empty(self):
        import tempfile

        self.mock_client.list_objects_v2.return_value = {
            "Contents": [{"Key": "data/orders.csv"}, {"Key": "data/products.csv"}]
        }
        with tempfile.TemporaryDirectory() as tmp:
            self.backend.sync_dir_down("data", tmp)

        self.mock_client.list_objects_v2.assert_called_once_with(
            Bucket="my-bucket", Prefix="data/"
        )
        downloaded = {c.args[1] for c in self.mock_client.download_file.call_args_list}
        self.assertEqual(downloaded, {"data/orders.csv", "data/products.csv"})


if __name__ == "__main__":
    unittest.main()
