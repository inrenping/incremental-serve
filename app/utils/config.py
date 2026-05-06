import os
import tempfile

TEMP_DIR = tempfile.gettempdir()
GARMIN_FIT_DIR = os.path.join(TEMP_DIR, "garmin-fit")
COROS_FIT_DIR = os.path.join(TEMP_DIR, "coros-fit")

# 自动创建
for d in [GARMIN_FIT_DIR, COROS_FIT_DIR]:
    os.makedirs(d, exist_ok=True)