"""Configuration management for KickDropsMiner"""
import json
import os
from utils.helpers import CONFIG_FILE


class Config:
    """Manages application configuration and queue items"""
    
    def __init__(self):
        self.items = []
        self.chromedriver_path = None
        self.extension_path = None
        self.mute = True
        self.hide_player = False
        self.mini_player = False
        self.force_160p = False
        self.dark_mode = True  # Dark by default
        self.theme_mode = "dark"  # auto | light | dark
        self.language = "fr"  # default language code
        self.auto_start = False  # Auto-start queue on launch
        self.debug = False  # Debug messages disabled by default
        self.accounts = []
        self.default_account_id = None
        self.account_selection_mode = "default"  # default | ask
        self.load()

    def load(self):
        """Load configuration from file"""
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.items = data.get("items", [])
            # Migrate old items format to new format with campaign info
            for item in self.items:
                if "campaign_id" not in item:
                    item["campaign_id"] = None
                if "campaign_channels" not in item:
                    item["campaign_channels"] = []
                if "required_category_id" not in item:
                    item["required_category_id"] = None
                if "is_global_drop" not in item:
                    item["is_global_drop"] = False
                if "cumulative_time" not in item:
                    item["cumulative_time"] = 0
                if "campaign_name" not in item:
                    item["campaign_name"] = None
                if "game" not in item:
                    item["game"] = None
                if "reward_names" not in item:
                    item["reward_names"] = []
                if "progress_units" not in item:
                    item["progress_units"] = 0
                if "claimed" not in item:
                    item["claimed"] = False
                if "account_id" not in item:
                    item["account_id"] = None
                if "is_manual_link" not in item:
                    item["is_manual_link"] = not bool(item.get("campaign_id") or item.get("campaign_name"))
                if "drop_name" not in item:
                    item["drop_name"] = "Manual link" if item.get("is_manual_link") else None
                if "watched_seconds" not in item:
                    item["watched_seconds"] = int(item.get("cumulative_time", 0) or 0)
                # Add tried_channels tracking to prevent switching loops
                if "tried_channels" not in item:
                    item["tried_channels"] = []
            self.chromedriver_path = data.get("chromedriver_path")
            self.extension_path = data.get("extension_path")
            self.mute = data.get("mute", True)
            self.hide_player = data.get("hide_player", False)
            self.mini_player = data.get("mini_player", False)
            self.force_160p = data.get("force_160p", False)
            self.dark_mode = data.get("dark_mode", True)
            self.theme_mode = data.get("theme_mode") or ("dark" if self.dark_mode else "light")
            if self.theme_mode not in {"auto", "light", "dark"}:
                self.theme_mode = "dark" if self.dark_mode else "light"
            self.language = data.get("language", "fr")
            self.auto_start = data.get("auto_start", False)
            self.debug = data.get("debug", False)
            self.accounts = data.get("accounts", [])
            self.default_account_id = data.get("default_account_id")
            self.account_selection_mode = data.get("account_selection_mode", "default")
            self._migrate_accounts()
        else:
            self.items = []
            self._migrate_accounts()

    def _migrate_accounts(self):
        if not isinstance(self.accounts, list):
            self.accounts = []
        normalized = []
        seen = set()
        for account in self.accounts:
            if not isinstance(account, dict):
                continue
            account_id = account.get("id")
            name = account.get("name")
            if not account_id or not name or account_id in seen:
                continue
            seen.add(account_id)
            normalized.append({"id": account_id, "name": name})
        self.accounts = normalized
        if not self.default_account_id or self.default_account_id not in {a["id"] for a in self.accounts}:
            self.default_account_id = self.accounts[0]["id"] if self.accounts else None

    def save(self):
        """Save configuration to file"""
        data = {
            "items": self.items,
            "chromedriver_path": self.chromedriver_path,
            "extension_path": self.extension_path,
            "mute": self.mute,
            "hide_player": self.hide_player,
            "mini_player": self.mini_player,
            "force_160p": self.force_160p,
            "dark_mode": self.dark_mode,
            "theme_mode": self.theme_mode,
            "language": self.language,
            "auto_start": self.auto_start,
            "debug": self.debug,
            "accounts": self.accounts,
            "default_account_id": self.default_account_id,
            "account_selection_mode": self.account_selection_mode,
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def add(
        self,
        url,
        minutes,
        campaign_id=None,
        campaign_channels=None,
        required_category_id=None,
        is_global_drop=False,
        campaign_name=None,
        game=None,
        reward_names=None,
        progress_units=0,
        claimed=False,
        account_id=None,
        is_manual_link=False,
        drop_name=None,
    ):
        """Add item with optional campaign grouping"""
        item = {
            "url": url,
            "minutes": minutes,
            "campaign_id": campaign_id,
            "campaign_channels": campaign_channels or [],
            "required_category_id": required_category_id,
            "is_global_drop": is_global_drop,
            "cumulative_time": 0,  # Track cumulative time across all streamers in campaign
            "campaign_name": campaign_name,
            "game": game,
            "reward_names": reward_names or [],
            "progress_units": progress_units,
            "claimed": claimed,
            "account_id": account_id or self.default_account_id,
            "is_manual_link": is_manual_link,
            "drop_name": drop_name,
            "watched_seconds": 0,
            "tried_channels": [],
        }
        self.items.append(item)
        self.save()

    def remove(self, idx):
        """Remove item at index"""
        del self.items[idx]
        self.save()

    def add_account(self, name):
        base = "".join(ch.lower() if ch.isalnum() else "_" for ch in name).strip("_") or "account"
        existing = {account["id"] for account in self.accounts}
        account_id = base
        n = 2
        while account_id in existing:
            account_id = f"{base}_{n}"
            n += 1
        self.accounts.append({"id": account_id, "name": name})
        if not self.default_account_id:
            self.default_account_id = account_id
        self.save()
        return account_id

    def remove_account(self, account_id):
        self.accounts = [account for account in self.accounts if account["id"] != account_id]
        replacement_id = self.accounts[0]["id"] if self.accounts else None
        if self.default_account_id == account_id:
            self.default_account_id = replacement_id
        for item in self.items:
            if item.get("account_id") == account_id:
                item["account_id"] = self.default_account_id
        self.save()
        return True

    def update_account_name(self, account_id, name):
        clean_name = (name or "").strip()
        if not clean_name:
            return False
        for account in self.accounts:
            if account["id"] == account_id:
                account["name"] = clean_name
                self.save()
                return True
        return False

