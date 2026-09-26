"""Build a demo user for Keep Backup's screenshots, inside a user + mount +
UTS namespace (see packaging/screenshots.sh): /home/alex with sample files and app data, a
"Backup Drive" at /run/media/alex, and real Borg backups made by Keep's own
engine, integrity check and recovery test. Nothing of the real user's is
read or touched."""
import datetime
import json
import os
from pathlib import Path
import random
import subprocess
import sys

APP = Path(sys.argv[1])
sys.path.insert(0, str(APP))
HOME = Path("/home/alex")
DRIVE = Path("/run/media/alex/Backup Drive")
REPO = DRIVE / "Keep"
PASSPHRASE = "demo-screenshots-only-7c1f"
random.seed(4)


def write(rel, size=None, text=None):
    path = HOME / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        path.write_text(text)
    else:
        path.write_bytes(random.randbytes(size))


def sample_files():
    write("Documents/Budget 2026.ods", 48_000)
    write("Documents/Lease agreement.pdf", 310_000)
    write("Documents/Taxes/2025 return.pdf", 520_000)
    write("Documents/Recipes/Sourdough.md", text="# Sourdough\n\n500 g flour, 350 g water, 100 g starter, 10 g salt.\n")
    write("Documents/Notes.txt", text="Call the plumber on Tuesday.\n")
    for n in range(24):
        write(f"Pictures/2026 Lisbon/IMG_{4100 + n}.jpg", 180_000 + n * 3_000)
    for n in range(8):
        write(f"Pictures/Family/IMG_{2200 + n}.jpg", 150_000)
    write("Music/Field recordings/Rain on the roof.flac", 900_000)
    write("Projects/website/index.html", text="<!doctype html><title>Alex</title><h1>Hello</h1>\n")
    write("Projects/website/style.css", text="body { font-family: sans-serif; }\n")
    write("Desktop/Packing list.txt", text="Passport\nCharger\n")
    # application data and settings, where the apps keep them
    write(".mozilla/firefox/k3v9x2.default-release/prefs.js", text='user_pref("browser.startup.page", 3);\n')
    write(".mozilla/firefox/k3v9x2.default-release/places.sqlite", 2_400_000)
    write(".thunderbird/q8m1d0.default/prefs.js", text='user_pref("mail.shell.checkDefaultClient", false);\n')
    write(".config/GIMP/2.10/gimprc", text="(theme \"Dark\")\n")
    write(".local/share/krita/brushes/ink.kpp", 12_000)
    write(".config/kritarc", text="[General]\n")
    write(".config/vlc/vlcrc", text="[qt]\n")
    write(".config/kdeglobals", text="[General]\nColorScheme=BreezeLight\n")
    # Flatpak apps the host has installed system-wide, so they count as installed
    write(".var/app/org.gimp.GIMP/config/GIMP/3.0/gimprc", text="(theme \"Default\")\n")
    write(".var/app/org.kde.krita/data/krita/brushes/pencil.kpp", 9_000)
    write(".var/app/io.mpv.Mpv/config/mpv/mpv.conf", text="volume=70\n")
    write(".var/app/com.spotify.Client/config/spotify/prefs", text="ui.show_friend_feed=false\n")
    write(".var/app/com.github.tchx84.Flatseal/data/flatseal/overrides.json", text="{}\n")


def environment():
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("BORG_", "XDG_", "KEEP_", "DBUS_"))}
    env.update(HOME=str(HOME), USER="alex", LOGNAME="alex",
               PATH=f"{Path(__file__).parent / 'bin'}:{env['PATH']}")
    return env


def main():
    sample_files()
    os.chmod(HOME, 0o700)
    env = environment()
    borg_dir = HOME / ".config/borg"
    borg_dir.mkdir(parents=True)
    (borg_dir / "passphrase").write_text(PASSPHRASE)
    os.chmod(borg_dir / "passphrase", 0o600)
    DRIVE.mkdir(parents=True)

    import consumer
    config = consumer.default_config(str(HOME))
    config["backup_sources"] = [{"label": name, "path": str(HOME / name)}
                                for name in ("Documents", "Pictures", "Desktop", "Music", "Projects")]
    config.update(setup_complete=True, archive_prefix="keep-workstation-",
                  destination={"type": "other", "label": "Backup Drive", "repo": str(REPO)})
    config["schedule"]["enabled"] = True
    config_path = HOME / ".config/keep/config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(config, indent=2))

    borg_env = dict(env, BORG_PASSPHRASE=PASSPHRASE)
    subprocess.run(["borg", "init", "--encryption=repokey-blake2", str(REPO)], env=borg_env, check=True)
    # A week of nightly backups before today, as the 04:00 timer makes them.
    sources = [entry["path"] for entry in config["backup_sources"]] + consumer.app_data_sources(config, str(HOME))
    sources = [path for path in consumer.dedupe_paths(sources) if os.path.exists(path)]
    today = datetime.date.today()
    for back in range(6, 0, -1):
        day = today - datetime.timedelta(days=back)
        write(f"Pictures/2026 Lisbon/IMG_{4200 + back}.jpg", 200_000)
        write("Documents/Notes.txt", text=f"Call the plumber on Tuesday.\nUpdated {day}.\n")
        stamp = datetime.datetime.combine(day, datetime.time(4, 0, 3 + back)).astimezone()
        name = f"keep-workstation-{stamp.strftime('%Y-%m-%d_%H%M%S')}"
        utc = stamp.astimezone(datetime.timezone.utc).replace(tzinfo=None)  # borg reads --timestamp as UTC
        subprocess.run(["borg", "create", "--timestamp", utc.isoformat(), "--exclude-caches", "--exclude", str(HOME / ".config/borg"),
                        f"{REPO}::{name}", *sources], env=borg_env, check=True)
    # Today: Keep's own engine, integrity check and recovery test.
    write("Documents/Budget 2026.ods", 49_000)
    subprocess.run([sys.executable, str(APP / "keep_backup.py"), "--config", str(config_path)],
                   env=env, check=True, capture_output=True)
    subprocess.run([sys.executable, str(APP / "cli.py"), "check"], env=env, check=True, capture_output=True)
    os.environ.clear()
    os.environ.update(env)
    import recovery_test
    result = recovery_test.run(str(REPO), PASSPHRASE)
    assert result["result"] == "passed", result
    recovery_test.record(result)
    recovery_test.save_access(str(REPO), {key: recovery_test.now_iso() for key in recovery_test.CONFIRMATIONS},
                              repository_id=result["repository_id"])
    listing = subprocess.run(["borg", "list", "--short", str(REPO)], env=borg_env, capture_output=True, text=True)
    print("archives:", listing.stdout.split())


if __name__ == "__main__":
    main()
