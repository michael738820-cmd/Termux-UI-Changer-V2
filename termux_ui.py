#!/usr/bin/env python3
"""Termux UI Changer: safe, backup-first Termux customization utility."""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

VERSION = "2.0.0"
REPOSITORY = "michael738820-cmd/Termux-UI-Changer-V2"
GITHUB_RAW = f"https://raw.githubusercontent.com/{REPOSITORY}/main/termux_ui.py"

RESET = "\033[0m"
CYAN = "\033[1;36m"
YELLOW = "\033[1;33m"
GREEN = "\033[1;32m"
RED = "\033[1;31m"

COLOR_SCHEMES = {
    "1": ("Monokai Dark", "#272822", "#f8f8f2", "#a6e22e"),
    "2": ("Dracula", "#282a36", "#f8f8f2", "#bd93f9"),
    "3": ("Matrix Green", "#000000", "#00ff00", "#003300"),
    "4": ("Nord", "#2e3440", "#d8dee9", "#88c0d0"),
    "5": ("Cyberpunk Yellow", "#101010", "#fcee0a", "#00f0ff"),
    "6": ("Gruvbox Dark", "#282828", "#ebdbb2", "#fabd2f"),
    "7": ("Tokyo Night", "#1a1b26", "#c0caf5", "#7aa2f7"),
    "8": ("Solarized Dark", "#002b36", "#839496", "#b58900"),
}

PROMPT_BEGIN = "# >>> Termux UI Changer managed prompt >>>"
PROMPT_END = "# <<< Termux UI Changer managed prompt <<<"
MOTD_BEGIN = "# >>> Termux UI Changer managed greeting >>>"
MOTD_END = "# <<< Termux UI Changer managed greeting <<<"


def termux_prefix() -> Path:
    return Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"))


def config_paths(home: Path | None = None, prefix: Path | None = None) -> dict[str, Path]:
    home = home or Path.home()
    prefix = prefix or termux_prefix()
    return {
        "bashrc": home / ".bashrc",
        "colors": home / ".termux" / "colors.properties",
        "motd": prefix / "etc" / "motd",
    }


def app_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".termux-ui-changer"


def backup_dir(home: Path | None = None) -> Path:
    return app_dir(home) / "backups"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def create_backup(paths: dict[str, Path] | None = None, home: Path | None = None) -> Path:
    """Snapshot managed config files; missing files are recorded for correct restore."""
    paths = paths or config_paths(home)
    folder = backup_dir(home)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    snapshot = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "files": {}}
    for key, path in paths.items():
        if path.is_file():
            snapshot["files"][key] = {
                "path": str(path),
                "exists": True,
                "content": base64.b64encode(path.read_bytes()).decode("ascii"),
                "mode": path.stat().st_mode & 0o777,
            }
        else:
            snapshot["files"][key] = {"path": str(path), "exists": False}
    destination = folder / f"backup-{stamp}.json"
    atomic_write(destination, json.dumps(snapshot, indent=2) + "\n")
    return destination


def list_backups(home: Path | None = None) -> list[Path]:
    folder = backup_dir(home)
    return sorted(folder.glob("backup-*.json"), reverse=True) if folder.exists() else []


def restore_backup(backup: Path, home: Path | None = None,
                   expected_paths: dict[str, Path] | None = None) -> list[str]:
    """Restore exactly the files in a snapshot, deleting files absent at snapshot time."""
    data = json.loads(backup.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("files"), dict):
        raise ValueError("Unsupported or invalid backup file")
    if any(not isinstance(item, dict) for item in data["files"].values()):
        raise ValueError("Backup file entries are malformed")
    restored: list[str] = []
    expected_paths = expected_paths or config_paths(home)
    if set(data["files"]) != set(expected_paths):
        raise ValueError("Backup does not match the expected Termux configuration files")
    current_paths = {}
    for key, item in data["files"].items():
        if Path(item.get("path", "")).resolve() != expected_paths[key].resolve():
            raise ValueError(f"Backup path validation failed for {key}")
        current_paths[key] = expected_paths[key]
    # Back up current state before restoring, so restore itself is reversible.
    create_backup(current_paths, home)
    for key, item in data["files"].items():
        path = Path(item["path"])
        if item.get("exists"):
            content = base64.b64decode(item["content"], validate=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temp_name, int(item.get("mode", 0o600)))
                os.replace(temp_name, path)
            except Exception:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
                raise
        else:
            path.unlink(missing_ok=True)
        restored.append(key)
    return restored


def bash_single_quote(value: str) -> str:
    """Return a safe Bash single-quoted literal."""
    return "'" + value.replace("'", "'\\''") + "'"


def update_prompt(name: str, bashrc: Path) -> None:
    name = name.strip()
    if (not name or len(name) > 40 or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
            or any(ch in "$`\\" for ch in name)):
        raise ValueError("Name must be 1–40 printable characters and cannot contain $, backticks, or backslashes")
    existing = bashrc.read_text(encoding="utf-8") if bashrc.exists() else ""
    # Replace only this app's previous block. User-defined prompt settings survive.
    pattern = re.compile(
        rf"(?ms)^\s*{re.escape(PROMPT_BEGIN)}\s*\n.*?^{re.escape(PROMPT_END)}\s*(?:\n|$)"
    )
    cleaned = pattern.sub("", existing).rstrip()
    prompt_value = f"\\[\\e[1;32m\\]{name}@termux\\[\\e[0m\\]:\\[\\e[1;34m\\]\\w\\[\\e[0m\\]\\$ "
    prompt = (
        f"{PROMPT_BEGIN}\n"
        f"export PS1={bash_single_quote(prompt_value)}\n"
        f"{PROMPT_END}\n"
    )
    atomic_write(bashrc, (cleaned + "\n\n" if cleaned else "") + prompt)


def update_motd(name: str, motd: Path) -> None:
    if not name or len(name) > 60 or any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise ValueError("Name must be 1–60 printable characters")
    existing = motd.read_text(encoding="utf-8") if motd.exists() else ""
    pattern = re.compile(
        rf"(?ms)^\s*{re.escape(MOTD_BEGIN)}\s*\n.*?^{re.escape(MOTD_END)}\s*(?:\n|$)"
    )
    cleaned = pattern.sub("", existing).rstrip()
    greeting = f"{MOTD_BEGIN}\nWelcome to Termux, {name}!\n{MOTD_END}\n"
    atomic_write(motd, (cleaned + "\n\n" if cleaned else "") + greeting)


def apply_color_scheme(choice: str, color_file: Path) -> str:
    if choice not in COLOR_SCHEMES:
        raise ValueError("Unknown theme selection")
    theme, bg, fg, cursor = COLOR_SCHEMES[choice]
    color_file.parent.mkdir(parents=True, exist_ok=True)
    old_lines = color_file.read_text(encoding="utf-8").splitlines() if color_file.exists() else []
    values = {"background": bg, "foreground": fg, "cursor": cursor}
    seen: set[str] = set()
    output: list[str] = []
    for line in old_lines:
        match = re.match(r"^\s*(background|foreground|cursor)\s*=", line)
        if match:
            key = match.group(1)
            if key not in seen:
                output.append(f"{key}={values[key]}")
                seen.add(key)
        else:
            output.append(line)
    for key in ("background", "foreground", "cursor"):
        if key not in seen:
            output.append(f"{key}={values[key]}")
    atomic_write(color_file, "\n".join(output).rstrip() + "\n")
    return theme


def reload_settings() -> tuple[bool, str]:
    command = shutil.which("termux-reload-settings")
    if not command:
        return False, "termux-reload-settings was not found; install or update the termux-tools package."
    try:
        result = subprocess.run([command], check=False, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return True, "Termux settings reloaded."
        detail = (result.stderr or result.stdout).strip()
        return False, f"Reload failed (exit {result.returncode}){': ' + detail if detail else ''}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Could not reload settings: {exc}"


def diagnostics(paths: dict[str, Path] | None = None) -> list[tuple[str, bool, str]]:
    paths = paths or config_paths()
    is_termux = bool(os.environ.get("PREFIX")) or str(termux_prefix()).startswith("/data/data/com.termux/")
    results = [
        ("Python", sys.version_info >= (3, 10), sys.version.split()[0]),
        ("Termux environment", is_termux, str(termux_prefix())),
        ("Home directory", Path.home().exists(), str(Path.home())),
    ]
    for label, key in (("Bash config", "bashrc"), ("Color config", "colors"), ("MOTD", "motd")):
        path = paths[key]
        parent_ok = path.parent.exists() and os.access(path.parent, os.W_OK)
        file_ok = not path.exists() or os.access(path, os.W_OK)
        results.append((label, parent_ok and file_ok, f"{path} ({'exists' if path.exists() else 'not created yet'})"))
    results.append(("Reload command", shutil.which("termux-reload-settings") is not None,
                    shutil.which("termux-reload-settings") or "not installed / not on PATH"))
    results.append(("Backup folder", True, str(backup_dir())))
    return results


def check_for_updates(timeout: float = 5.0) -> tuple[str | None, str]:
    request = urllib.request.Request(GITHUB_RAW, headers={"User-Agent": f"Termux-UI-Changer/{VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            source = response.read(512_000).decode("utf-8", errors="replace")
        match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
        if not match:
            return None, "Could not read the version from the repository."
        latest = match.group(1)
        def version_tuple(value: str) -> tuple[int, ...]:
            try:
                return tuple(int(part) for part in value.split("."))
            except ValueError:
                return ()
        installed_parts, latest_parts = version_tuple(VERSION), version_tuple(latest)
        if not installed_parts or not latest_parts:
            return latest, "Version comparison unavailable; compare versions manually."
        width = max(len(installed_parts), len(latest_parts))
        installed_parts += (0,) * (width - len(installed_parts))
        latest_parts += (0,) * (width - len(latest_parts))
        return latest, "A newer version is available." if latest_parts > installed_parts else "You are using the latest version or a newer development build."
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, f"Update check unavailable: {exc}"


def pause() -> None:
    input("\nPress Enter to continue...")


def print_banner() -> None:
    print(f"{CYAN}╔══════════════════════════════════════════╗\n║       TERMUX UI CHANGER  v{VERSION:<17}║\n║       Themes • Backup • Restore • Tools   ║\n╚══════════════════════════════════════════╝{RESET}\n")


def _backup_before_change(paths: dict[str, Path]) -> Path:
    backup = create_backup(paths)
    print(f"{GREEN}[+] Safety backup: {backup}{RESET}")
    return backup


def menu(home: Path | None = None, prefix: Path | None = None,
         input_fn: Callable[[str], str] = input) -> None:
    paths = config_paths(home, prefix)
    while True:
        os.system("clear")
        print_banner()
        print(f" {YELLOW}[1]{RESET} Set display name & shell prompt")
        print(f" {YELLOW}[2]{RESET} Choose color theme")
        print(f" {YELLOW}[3]{RESET} Create backup now")
        print(f" {YELLOW}[4]{RESET} Restore a backup")
        print(f" {YELLOW}[5]{RESET} Run diagnostics")
        print(f" {YELLOW}[6]{RESET} Check for updates")
        print(f" {YELLOW}[7]{RESET} Reload Termux settings")
        print(f" {YELLOW}[8]{RESET} Exit")
        choice = input_fn("\nSelect an option: ").strip()
        try:
            if choice == "1":
                name = input_fn("Display name (1–40 characters): ").strip()
                if (not name or len(name) > 40 or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
                        or any(ch in "$`\\" for ch in name)):
                    raise ValueError("Name must be 1–40 printable characters and cannot contain $, backticks, or backslashes")
                _backup_before_change(paths)
                update_prompt(name, paths["bashrc"])
                try:
                    update_motd(name, paths["motd"])
                    print(f"{GREEN}[+] Prompt and startup greeting updated.{RESET}")
                except OSError as exc:
                    print(f"{YELLOW}[!] Prompt updated, but MOTD could not be changed: {exc}{RESET}")
            elif choice == "2":
                for key, (theme, *_colors) in COLOR_SCHEMES.items():
                    print(f" {YELLOW}[{key}]{RESET} {theme}")
                theme_choice = input_fn("Theme: ").strip()
                if theme_choice not in COLOR_SCHEMES:
                    raise ValueError("Invalid theme selection")
                _backup_before_change(paths)
                theme = apply_color_scheme(theme_choice, paths["colors"])
                ok, message = reload_settings()
                print(f"{GREEN}[+] {theme} applied. {message}{RESET}" if ok else f"{YELLOW}[!] {theme} saved. {message}{RESET}")
            elif choice == "3":
                print(f"{GREEN}[+] Backup created: {create_backup(paths)}{RESET}")
            elif choice == "4":
                backups = list_backups()
                if not backups:
                    print(f"{YELLOW}No backups found yet.{RESET}")
                else:
                    for index, path in enumerate(backups, 1):
                        print(f" [{index}] {path.name}")
                    selected = input_fn("Backup number (0 to cancel): ").strip()
                    if selected != "0":
                        index = int(selected) - 1
                        if not 0 <= index < len(backups):
                            raise ValueError("Invalid backup number")
                        confirm = input_fn("Restore this backup? This replaces current config. Type YES: ")
                        if confirm == "YES":
                            print(f"{GREEN}[+] Restored: {', '.join(restore_backup(backups[index], expected_paths=paths))}{RESET}")
                        else:
                            print("Restore cancelled.")
            elif choice == "5":
                for label, ok, detail in diagnostics(paths):
                    status = f"{GREEN}OK{RESET}" if ok else f"{RED}CHECK{RESET}"
                    print(f" [{status}] {label}: {detail}")
            elif choice == "6":
                latest, message = check_for_updates()
                print(f"{message} Installed: {VERSION}" + (f" | Repository: {latest}" if latest else ""))
                print("Updates are check-only in this release; review the repository before replacing files.")
            elif choice == "7":
                ok, message = reload_settings()
                print(f"{GREEN if ok else YELLOW}{message}{RESET}")
            elif choice == "8":
                print("\nExiting Termux UI Changer. Restart your shell to see prompt changes.")
                return
            else:
                print(f"{RED}Invalid selection.{RESET}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"{RED}[!] {exc}{RESET}")
        pause()


def main() -> None:
    try:
        menu()
    except (KeyboardInterrupt, EOFError):
        print("\nExited safely.")


if __name__ == "__main__":
    main()
