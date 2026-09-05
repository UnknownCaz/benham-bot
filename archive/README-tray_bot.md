# tray_bot.ps1 - ARCHIVED 2026-09-05 (Benham Phase B, INTENT decision 47)

The Windows tray icon for the PC-hosted Benham face: status rows, the unread-DM
badge, Restart/Stop hands (Stop-Process on the bot pid, schtasks on the logon
task), the guest-OFF panic button, and the log viewer. It could only ever watch
a PC bot, and the bot moved to cazzy-mac under launchd on 2026-09-05.

Each of its jobs has a decided home or a decided death (DECISIONS.md D6-D10):
guest OFF -> the `guest_off` capability (`benham.py guest off`, or a DM);
restart -> the `restart` capability; the unread badge -> Discord's own badge;
status -> `benham.py status` / the console tile; logs -> `benham.py usage`,
`benham.py inbox`, and the Mac's logs/benham.log.

Revival (never expected): re-enable the `benham-bot-tray` logon task
(scripts/benham-bot-tray.task.backup.xml is the XML) and move this file back -
but it only makes sense with a bot on the PC again, which means the whole
Phase B revival path first (INTENT 42: bootout the Mac daemon, reset the token).
