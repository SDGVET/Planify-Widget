# Planify Tasks

A KDE Plasma 6 widget that shows your [Planify](https://github.com/alainm23/planify) tasks.

- **In a panel:** a checklist icon with a badge for today's tasks. The badge turns to the theme's warning color when something is overdue. Click it to see overdue and today's tasks.
- **On the desktop:** a resizable list with Overdue, Today, Due soon and Important sections.

Clicking a task or **Open Planify** opens Planify. The widget refreshes every minute and just after midnight.

## Requirements

- KDE Plasma 6
- Planify installed as a Flatpak (`io.github.alainm23.planify`)
- `python3`

## Install

```bash
./install.sh
```

Run it again to upgrade. If the old version is still showing after an upgrade, log out and back in.

## Add the widget

- **Panel:** right-click the panel, choose **Add or Manage Widgets**, then add **Planify Tasks**.
- **Desktop:** right-click the desktop, choose **Enter Edit Mode** or **Add Widgets**, then drag **Planify Tasks** onto the desktop.

## Settings

Right-click the widget and choose **Configure**.

| Setting | Default | Range |
| --- | --- | --- |
| Due soon window | 3 days | 1 to 30 days |
| Important priority | P1 only | P1, P2 and higher, or P3 and higher |
| Warn when closed and unchanged | 60 minutes | 5 to 1440 minutes |

If you use a Nextcloud account, a warning appears when Planify is closed and its data has not changed for the configured time.

## Privacy

- Reads Planify's local database read-only.
- Never reads stored account credentials.
- Makes no network connections.
- Never creates, changes or deletes tasks.

## Uninstall

```bash
./install.sh --uninstall
```

## Tests

```bash
python3 -m pytest
```

## License

[MIT](LICENSE)
