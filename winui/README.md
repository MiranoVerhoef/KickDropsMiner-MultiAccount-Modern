# Kick Drop Miner WinUI 3 Migration

This folder contains the native Windows UI migration.

Current state:

- `KickDropsMiner.WinUI` is a real WinUI 3 / Windows App SDK project.
- The app has native pages for Main Menu, Browse Drops, Logging, and Accounts.
- It uses native WinUI controls for the main queue, campaign browser, logging, and account/settings UI.
- `winui_bridge.py` runs as a long-lived JSON-lines bridge process.
- `core/winui_service.py` owns queue start/stop/skip, persisted watch progress, campaign fetching, account add/remove, account cookie login, and runtime log events.
- Selenium and Kick-specific automation remain in the existing Python `core` modules and are called from the bridge. The old CustomTkinter UI remains untouched.

Remaining migration work:

- Package the WinUI app plus Python backend as one release artifact.
- Add richer Browse Drops grouping/images if needed. The current native page is a fast flat campaign list.
- Move any remaining duplicated helper logic from `ui/app.py` into shared backend modules as the old UI is retired.
- Add integration tests for bridge commands before replacing the Python UI as the default entry point.

Build:

```powershell
dotnet build .\winui\KickDropsMiner.WinUI\KickDropsMiner.WinUI.csproj -c Debug
```
