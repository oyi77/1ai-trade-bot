import zipfile
from unittest.mock import MagicMock, patch

import pytest

from tradebot.services.backup_service import execute_backup


@pytest.mark.asyncio
async def test_execute_backup(tmp_path):
    # Setup mock data directory
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_file = data_dir / "test.db"
    db_file.write_text("dummy data")

    with (
        patch("tradebot.services.backup_service.settings") as mock_settings,
        patch("tradebot.services.backup_service.urllib.request.urlopen") as mock_urlopen,
    ):
        mock_settings.DATA_DIR = str(data_dir)
        mock_settings.ADMIN_USER_IDS = "12345"
        mock_settings.TELEGRAM_BOT_TOKEN = "dummy_token"

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        await execute_backup()

        # Verify zip was created
        backup_dir = data_dir.parent / "backups"
        assert backup_dir.exists()
        zips = list(backup_dir.glob("vilona_backup_*.zip"))
        assert len(zips) == 1

        # Verify zip contents
        with zipfile.ZipFile(zips[0], "r") as zf:
            assert "data/test.db" in zf.namelist()

        # Verify telegram upload was called
        mock_urlopen.assert_called_once()
        args, kwargs = mock_urlopen.call_args
        assert "botdummy_token/sendDocument" in args[0].full_url
