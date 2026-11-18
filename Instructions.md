# Termux UI Changer — Quick Start

1. Update package information and install the requirements:

   ```sh
   pkg update
   pkg install python git -y
   ```

2. Clone the repository and open its folder:

   ```sh
   git clone https://github.com/michael738820-cmd/Termux-UI-Changer-V2.git
   cd Termux-UI-Changer-V2
   ```

3. Start the app:

   ```sh
   python termux_ui.py
   ```

Requires Python 3.10 or newer. No `pip install`, `termux-setup-storage`, `ncurses-utils`, or root access is needed.

## Safety and recovery

- The app creates a backup before changing the prompt, MOTD, or colors.
- Use **Restore a backup** from the menu to roll back a previous change.
- Backups live in `~/.termux-ui-changer/backups/`.
- Restart your shell after changing the prompt.

## Run the test suite

```sh
python -m unittest discover -s tests -v
```
