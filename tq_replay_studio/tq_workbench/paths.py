from pathlib import Path
import os


def data_directory():
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    folder = base / "TqReplayStudio"
    folder.mkdir(parents=True, exist_ok=True)
    return folder
