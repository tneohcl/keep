"""`python -m keep_backup`: the Keep Backup window."""
from keep_backup.ui import app_logging

# Before the window module loads, so problems while importing it are logged too.
app_logging.start()

from keep_backup.ui.main_window import main  # noqa: E402

main()
