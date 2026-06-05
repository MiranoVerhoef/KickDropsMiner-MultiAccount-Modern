"""UI-independent backend service used by the WinUI frontend."""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from urllib.parse import urlparse

from core import (
    Config,
    CookieManager,
    StreamWorker,
    make_chrome_driver,
    kick_is_live_by_api,
    claim_available_drops,
    fetch_drops_campaigns_and_progress,
    fetch_drops_progress,
    is_campaign_expired,
)
from core.browser import accept_kick_cookies
from utils.helpers import (
    DATA_DIR,
    cookie_file_for_account,
    debug_print,
    domain_from_url,
    set_debug_config,
)

DROPS_CACHE_FILE = os.path.join(DATA_DIR, "winui_drops_cache.json")


class WinUIBackend:
    """Small command service that owns queue workers outside any UI toolkit."""

    def __init__(self, emit):
        self.emit = emit
        self.config = Config()
        set_debug_config(self.config)
        self.workers = {}
        self.queue_running = False
        self.queue_current_idx = None
        self._ignored_finishes = set()
        self._last_progress_log = {}
        self._login_drivers = {}
        self._status = "Ready"
        self._lock = threading.RLock()
        self._actions = queue.Queue()
        self._running = True
        threading.Thread(target=self._action_loop, daemon=True).start()
        threading.Thread(target=self._offline_retry_loop, daemon=True).start()

    def handle(self, command, payload=None):
        payload = payload or {}
        handlers = {
            "state": self.state,
            "cached_drops": self.cached_drops,
            "add_manual": self.add_manual,
            "edit_manual": self.edit_manual,
            "fetch_drops": self.fetch_drops,
            "add_campaign": self.add_campaign,
            "remove": self.remove,
            "start_queue": self.start_queue,
            "stop_queue": self.stop_queue,
            "skip_creator": self.skip_creator,
            "remove_account": self.remove_account,
            "rename_account": self.rename_account,
            "start_login": self.start_login,
            "finish_login": self.finish_login,
            "cancel_login": self.cancel_login,
            "update_settings": self.update_settings,
            "shutdown": self.shutdown,
        }
        handler = handlers.get(command)
        if not handler:
            return {"ok": False, "error": f"Unknown command: {command}"}
        return handler(payload)

    def cached_drops(self, _payload=None):
        return {"ok": True, "campaigns": self._load_cached_campaigns(), "cached": True}

    def state(self, _payload=None):
        with self._lock:
            self.config.load()
            return {
                "ok": True,
                "status": self._status,
                "queue_running": self.queue_running,
                "queue_current_idx": self.queue_current_idx,
                "accounts": [
                    {
                        **account,
                        "cookies_valid": self._cookies_valid(account.get("id")),
                    }
                    for account in self.config.accounts
                ],
                "items": [
                    self._serialize_item(idx, item)
                    for idx, item in enumerate(self.config.items)
                    if not item.get("claimed")
                ],
                "settings": {
                    "mute": self.config.mute,
                    "hide_player": self.config.hide_player,
                    "mini_player": self.config.mini_player,
                    "force_160p": self.config.force_160p,
                    "auto_start": self.config.auto_start,
                    "dark_mode": self.config.dark_mode,
                    "theme_mode": self.config.theme_mode,
                    "language": self.config.language,
                    "chromedriver_path": self.config.chromedriver_path,
                    "extension_path": self.config.extension_path,
                },
            }

    def add_manual(self, payload):
        url = (payload.get("url") or "").strip()
        minutes = int(payload.get("minutes") or 0)
        account_id = payload.get("account_id")
        if not url:
            return {"ok": False, "error": "Missing url"}
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        if not domain_from_url(url):
            return {"ok": False, "error": "Invalid URL"}
        with self._lock:
            self.config.load()
            if not account_id and self.config.accounts:
                account_id = self.config.accounts[0].get("id")
            self.config.add(
                url,
                max(0, minutes),
                account_id=account_id,
                is_manual_link=True,
                drop_name="Manual link",
            )
            item = self.config.items[-1]
            self._log_item(item, f"Added manual link for {self._streamer_name_from_url(url)}")
        return {"ok": True, **self.state()}

    def edit_manual(self, payload):
        idx = int(payload.get("index", -1))
        url = (payload.get("url") or "").strip()
        minutes = int(payload.get("minutes") or 0)
        account_id = payload.get("account_id")
        if not url:
            return {"ok": False, "error": "Missing url"}
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        if not domain_from_url(url):
            return {"ok": False, "error": "Invalid URL"}
        with self._lock:
            self.config.load()
            if idx < 0 or idx >= len(self.config.items):
                return {"ok": False, "error": "Invalid item index"}
            item = self.config.items[idx]
            if not item.get("is_manual_link"):
                return {"ok": False, "error": "Only manual links can be edited"}
            if idx in self.workers:
                return {"ok": False, "error": "Stop the queue before editing the active link"}
            item["url"] = url
            item["minutes"] = max(0, minutes)
            item["account_id"] = account_id or None
            item["drop_name"] = "Manual link"
            item["is_manual_link"] = True
            item["tried_channels"] = []
            self.config.save()
            self._log_item(item, f"Edited manual link for {self._streamer_name_from_url(url)}")
        return {"ok": True, **self.state()}

    def fetch_drops(self, _payload=None):
        driver = None
        try:
            self.emit({"type": "drops_begin", "message": "Loading Kick drops..."})
            result = fetch_drops_campaigns_and_progress()
            campaigns = result.get("campaigns", [])
            progress_data = [p for p in result.get("progress", []) if isinstance(p, dict)]
            driver = result.get("driver")
            progress_by_id = {p.get("id"): p for p in progress_data if p.get("id")}
            mapped = []
            total = len(campaigns)
            for campaign in campaigns:
                campaign_id = campaign.get("id")
                progress = progress_by_id.get(campaign_id)
                if progress:
                    campaign["progress_data"] = progress
                    campaign["progress_status"] = progress.get("status", "not_started")
                    campaign["progress_units"] = progress.get("progress_units", 0)
                    rewards_by_id = {
                        reward.get("id"): reward
                        for reward in progress.get("rewards", [])
                        if isinstance(reward, dict) and reward.get("id")
                    }
                    for reward in campaign.get("rewards", []):
                        progress_reward = rewards_by_id.get(reward.get("id"))
                        if progress_reward:
                            reward["progress"] = progress_reward.get("progress", 0.0)
                            reward["claimed"] = progress_reward.get("claimed", False)
                            reward["progress_required_units"] = progress_reward.get("required_units", 0)
                else:
                    campaign["progress_status"] = "not_started"
                    campaign["progress_units"] = 0
                    for reward in campaign.get("rewards", []):
                        reward["progress"] = 0.0
                        reward["claimed"] = False

                if is_campaign_expired(campaign):
                    continue
                serialized = self._serialize_campaign(campaign)
                mapped.append(serialized)
                self.emit({
                    "type": "drops_campaign",
                    "campaign": serialized,
                    "loaded": len(mapped),
                    "total": total,
                })
                time.sleep(0.035)
            self._save_cached_campaigns(mapped)
            self.emit({"type": "drops_end", "loaded": len(mapped), "total": total})
            return {"ok": True, "campaigns": mapped}
        except Exception as exc:
            self.emit({"type": "drops_error", "message": str(exc)})
            return {"ok": False, "error": str(exc), "campaigns": []}
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def add_campaign(self, payload):
        campaign = payload.get("campaign")
        account_id = payload.get("account_id")
        if not isinstance(campaign, dict):
            return {"ok": False, "error": "Missing campaign"}
        with self._lock:
            if not account_id and self.config.accounts:
                account_id = self.config.accounts[0].get("id")
            if not account_id:
                return {"ok": False, "error": "Add a Kick account before adding drops"}
            if not self._add_or_update_campaign(campaign, account_id):
                return {"ok": False, "error": "Campaign has no stream channels"}
            self._log(f"Added drop: {campaign.get('name', 'Unknown campaign')}", campaign.get("name", ""), "")
            return {"ok": True, **self.state()}

    def remove(self, payload):
        idx = int(payload.get("index", -1))
        with self._lock:
            if idx < 0 or idx >= len(self.config.items):
                return {"ok": False, "error": "Invalid item index"}
            if idx in self.workers:
                self._save_worker_progress(idx, "removed")
                self._ignored_finishes.add(idx)
                self.workers[idx].stop()
                self.workers.pop(idx, None)
            item = self.config.items[idx]
            self._log_item(item, f"Removed {self._drop_title_for_item(item)}")
            self.config.remove(idx)
            self._reindex_workers()
            return {"ok": True, **self.state()}

    def start_queue(self, _payload=None):
        with self._lock:
            if self.queue_running or self.workers:
                return self.stop_queue()
            self.queue_running = True
            self.queue_current_idx = None
            self._status = "Running"
            self._log("Queue started", "Queue", "")
            self._emit_state()
            self._actions.put(("run_from", 0))
            return {"ok": True, **self.state()}

    def stop_queue(self, _payload=None):
        with self._lock:
            self.queue_running = False
            self.queue_current_idx = None
            for idx, worker in list(self.workers.items()):
                self._save_worker_progress(idx, "queue stopped")
                self._ignored_finishes.add(idx)
                try:
                    worker.stop()
                except Exception:
                    pass
                self.workers.pop(idx, None)
            self._status = "Ready"
            self._log("Queue stopped", "Queue", "")
            return {"ok": True, **self.state()}

    def skip_creator(self, _payload=None):
        with self._lock:
            idx = self.queue_current_idx
            if idx is None and self.workers:
                idx = next(iter(self.workers.keys()))
            if idx is None or idx < 0 or idx >= len(self.config.items):
                return {"ok": False, "error": "No active creator to skip"}
            item = self.config.items[idx]
            current_url = item.get("url", "")
            self._log_item(item, f"Skipped creator: {self._streamer_name_from_url(current_url)}", current_url)

            tried = item.get("tried_channels", [])
            if current_url and current_url not in tried:
                tried.append(current_url)
            next_url = None
            for channel in item.get("campaign_channels", []):
                alt_url = channel.get("url") if isinstance(channel, dict) else channel
                if alt_url and alt_url != current_url and alt_url not in tried:
                    next_url = alt_url
                    tried.append(alt_url)
                    break
            item["tried_channels"] = tried
            if next_url:
                item["_manual_next_url"] = next_url
                self._log_item(item, f"Moving to next creator: {self._streamer_name_from_url(next_url)}", next_url)
            else:
                item["_manual_skip_drop"] = True
                self._log_item(item, f"No more creators for {self._drop_title_for_item(item)}; moving to next drop")
            self.config.save()
            worker = self.workers.get(idx)
            if worker:
                self._save_worker_progress(idx, "creator skipped")
                worker.stop()
            else:
                self._complete_manual_skip(idx)
            return {"ok": True, **self.state()}

    def remove_account(self, payload):
        account_id = payload.get("account_id")
        if not account_id:
            return {"ok": False, "error": "Missing account id"}
        with self._lock:
            for idx, worker in list(self.workers.items()):
                if idx < len(self.config.items) and self.config.items[idx].get("account_id") == account_id:
                    self._save_worker_progress(idx, "account removed")
                    self._ignored_finishes.add(idx)
                    worker.stop()
                    self.workers.pop(idx, None)
            self.config.items = [
                item for item in self.config.items
                if item.get("account_id") != account_id
            ]
            self.config.remove_account(account_id)
            self._log(f"Removed account: {account_id}", "Accounts", "")
            return {"ok": True, **self.state()}

    def rename_account(self, payload):
        account_id = payload.get("account_id")
        name = (payload.get("name") or "").strip()
        if not account_id or not name:
            return {"ok": False, "error": "Missing account id or name"}
        with self._lock:
            if not self.config.update_account_name(account_id, name):
                return {"ok": False, "error": "Account not found"}
            self._log(f"Renamed account to {name}", "Accounts", name)
            return {"ok": True, **self.state()}

    def start_login(self, payload):
        account_id = payload.get("account_id")
        login_id = account_id or f"new-{uuid.uuid4().hex}"
        try:
            driver = make_chrome_driver(
                headless=False,
                visible_width=520,
                visible_height=720,
                driver_path=self.config.chromedriver_path,
                extension_path=self.config.extension_path,
            )
            try:
                driver.set_window_size(520, 720)
                driver.set_window_position(80, 80)
            except Exception:
                pass
            driver.get("https://kick.com")
            self._login_drivers[login_id] = driver
            threading.Thread(target=lambda: self._accept_cookies_later(driver), daemon=True).start()
            label = self._account_name(account_id) if account_id else "new account"
            self._log(f"Please sign in to Kick for {label}.", "Accounts", label)
            return {"ok": True, "login_id": login_id, "account_id": account_id}
        except Exception as exc:
            return {"ok": False, "error": f"Chrome start failed: {exc}"}

    def finish_login(self, payload):
        login_id = payload.get("login_id") or payload.get("account_id")
        account_id = payload.get("account_id")
        fallback_name = (payload.get("account_name") or "").strip()
        if not login_id:
            return {"ok": False, "error": "Missing login id"}
        driver = self._login_drivers.get(login_id)
        if driver is None:
            return {"ok": False, "error": "No login session is active"}
        try:
            detected_name = self._detect_logged_in_kick_name(driver)
            account_name = detected_name or fallback_name
            if not account_name:
                return {"ok": False, "needs_name": True, "error": "Kick username could not be detected."}
            if account_id:
                CookieManager.save_cookies(driver, "kick.com", account_id)
                self.config.update_account_name(account_id, account_name)
            else:
                account_id = self.config.add_account(account_name)
                CookieManager.save_cookies(driver, "kick.com", account_id)
            self._login_drivers.pop(login_id, None)
            self._log(f"Cookies saved for {account_name}", "Accounts", account_name)
            return {"ok": True, "account_id": account_id, "account_name": account_name, **self.state()}
        except Exception as exc:
            return {"ok": False, "error": f"Could not save cookies: {exc}"}
        finally:
            if login_id not in self._login_drivers:
                try:
                    driver.quit()
                except Exception:
                    pass

    def cancel_login(self, payload):
        login_id = payload.get("login_id") or payload.get("account_id")
        driver = self._login_drivers.pop(login_id, None)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        return {"ok": True}

    def update_settings(self, payload):
        with self._lock:
            for key in ("mute", "hide_player", "mini_player", "force_160p", "auto_start", "dark_mode"):
                if key in payload:
                    setattr(self.config, key, bool(payload[key]))
            for key in ("language", "chromedriver_path", "extension_path"):
                if key in payload:
                    setattr(self.config, key, payload[key])
            if "theme_mode" in payload:
                theme_mode = payload.get("theme_mode")
                self.config.theme_mode = theme_mode if theme_mode in {"auto", "light", "dark"} else "dark"
                self.config.dark_mode = self.config.theme_mode == "dark"
            self.config.save()
            for worker in list(self.workers.values()):
                worker.mute = self.config.mute
                worker.hide_player = self.config.hide_player
                worker.mini_player = self.config.mini_player
                worker.force_160p = self.config.force_160p
            return {"ok": True, **self.state()}

    def shutdown(self, _payload=None):
        self.stop_queue()
        for driver in list(self._login_drivers.values()):
            try:
                driver.quit()
            except Exception:
                pass
        self._login_drivers.clear()
        self._running = False
        return {"ok": True}

    def _action_loop(self):
        while self._running:
            try:
                action, value = self._actions.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if action == "run_from":
                    self._run_queue_from(int(value))
                elif action == "start_index":
                    self._start_index(int(value))
            except Exception as exc:
                self._log(f"Backend error: {exc}", "Backend", "")
                self._emit_state()

    def _run_queue_from(self, start_idx):
        with self._lock:
            if self.workers:
                return
            self.config.load()
            for idx in range(start_idx, len(self.config.items)):
                item = self.config.items[idx]
                if item.get("finished") or item.get("claimed"):
                    continue
                self.queue_current_idx = idx
                self._status = f"Starting {self._streamer_name_from_url(item.get('url', ''))}"
                self._emit_state()
                self._actions.put(("start_index", idx))
                return
            self.queue_running = False
            self.queue_current_idx = None
            self._status = "Ready"
            self._prune_claimed_items()
            self._log("Queue finished", "Queue", "")
            self._emit_state()

    def _start_index(self, idx):
        with self._lock:
            if idx < 0 or idx >= len(self.config.items):
                return
            if self.workers:
                return
            item = self.config.items[idx]

        if not kick_is_live_by_api(item["url"]):
            self._log_item(item, f"{self._streamer_name_from_url(item['url'])} is offline")
            if self._switch_to_live_alternative(idx):
                time.sleep(8)
                self._actions.put(("start_index", idx))
                return
            self._log_item(item, f"No live streamer available for {self._drop_title_for_item(item)}; waiting to retry")
            with self._lock:
                self._status = "Waiting for live streamer"
                self._emit_state()
            return

        domain = domain_from_url(item["url"])
        if not domain:
            self._log_item(item, "Invalid URL")
            return
        if not self._ensure_cookies(item, domain):
            return

        with self._lock:
            saved_seconds = int(item.get("cumulative_time" if item.get("is_global_drop") else "watched_seconds", 0) or 0)
            target_seconds = int(item.get("minutes", 0) or 0) * 60
            remaining_minutes = item.get("minutes", 0) or 0
            if target_seconds:
                remaining_seconds = max(0, target_seconds - saved_seconds)
                if remaining_seconds <= 0:
                    item["finished"] = True
                    self.config.save()
                    self._log_item(item, f"Already complete at {self._format_duration(saved_seconds)} watched")
                    self._emit_state()
                    self._actions.put(("run_from", idx + 1))
                    return
                remaining_minutes = remaining_seconds / 60
                self._log_item(item, f"Resuming at {self._format_duration(saved_seconds)} watched; {self._format_duration(remaining_seconds)} left")
            else:
                self._log_item(item, f"Resuming manual watch at {self._format_duration(saved_seconds)} watched")

            cumulative_cb = None
            if item.get("is_global_drop"):
                campaign_id = item.get("campaign_id")

                def cumulative_time():
                    return sum(
                        int(other.get("cumulative_time", 0) or 0)
                        for other in self.config.items
                        if other.get("campaign_id") == campaign_id
                    ) - saved_seconds

                cumulative_cb = cumulative_time

            stop_event = threading.Event()
            worker = StreamWorker(
                item["url"],
                remaining_minutes,
                on_update=lambda seconds, live, i=idx: self._on_worker_update(i, seconds, live),
                on_finish=lambda elapsed, completed, i=idx: self._on_worker_finish(i, elapsed, completed),
                stop_event=stop_event,
                driver_path=self.config.chromedriver_path,
                extension_path=self.config.extension_path,
                hide_player=bool(self.config.hide_player),
                mute=bool(self.config.mute),
                mini_player=bool(self.config.mini_player),
                force_160p=bool(self.config.force_160p),
                required_category_id=item.get("required_category_id"),
                cumulative_time_callback=cumulative_cb,
                account_id=item.get("account_id") or self.config.default_account_id,
            )
            self.workers[idx] = worker
            self.queue_current_idx = idx
            self._status = f"Watching {self._streamer_name_from_url(item['url'])}"
            self._log_item(item, f"Start watching stream: {self._streamer_name_from_url(item['url'])} for {self._drop_title_for_item(item)}")
            self._emit_state()
            worker.start()

    def _on_worker_update(self, idx, seconds, live):
        with self._lock:
            if idx < 0 or idx >= len(self.config.items):
                return
            item = self.config.items[idx]
            worker = self.workers.get(idx)
            progress = self._progress_text_for_seconds(item, seconds)
            status = "LIVE" if live else "Paused"
            self._status = f"{self._streamer_name_from_url(item.get('url', ''))}: {progress} ({status})"
            self._maybe_log_progress(idx)
            self.emit({
                "type": "progress",
                "state": self.state(),
            })

    def _on_worker_finish(self, idx, elapsed, completed):
        with self._lock:
            if idx in self._ignored_finishes:
                self._ignored_finishes.discard(idx)
                return
            if idx < 0 or idx >= len(self.config.items):
                return
            if self._complete_manual_skip(idx):
                return
            worker = self.workers.get(idx)
            ended_offline = bool(worker and getattr(worker, "ended_because_offline", False))
            ended_wrong_category = bool(worker and getattr(worker, "ended_because_wrong_category", False))
            self.workers.pop(idx, None)
            item = self.config.items[idx]
            elapsed = int(elapsed or 0)
            completed_value = completed
            campaign_id = item.get("campaign_id")

            if item.get("is_global_drop") and campaign_id:
                for other in self.config.items:
                    if other.get("campaign_id") == campaign_id:
                        other["cumulative_time"] = int(other.get("cumulative_time", 0) or 0) + elapsed
                        other["watched_seconds"] = other["cumulative_time"]
                target_minutes = int(item.get("minutes", 0) or 0)
                completed_value = bool(target_minutes and item.get("cumulative_time", 0) // 60 >= target_minutes)
                if completed_value:
                    for other in self.config.items:
                        if other.get("campaign_id") == campaign_id:
                            other["finished"] = True
            elif elapsed > 0:
                item["watched_seconds"] = int(item.get("watched_seconds", 0) or 0) + elapsed

            if completed_value:
                item["finished"] = True
                item["tried_channels"] = []
                self.config.save()
                self._log_item(item, f"Finished watching {self._drop_title_for_item(item)} at {self._progress_text_for_seconds(item, 0)}")
                if campaign_id:
                    threading.Thread(target=self._sync_claimed, args=(campaign_id, item.get("account_id")), daemon=True).start()
            elif ended_offline or ended_wrong_category:
                reason = "wrong category" if ended_wrong_category else "streamer went offline"
                self._log_item(item, f"{reason.capitalize()} for {self._streamer_name_from_url(item['url'])}")
                if self._switch_to_live_alternative(idx):
                    time.sleep(8)
                    self._actions.put(("start_index", idx))
                    self._emit_state()
                    return
                self._log_item(item, f"No live alternative found for {self._drop_title_for_item(item)}; waiting to retry")
                self.config.save()
            else:
                self.config.save()

            self._emit_state()
            if self.queue_running and self.queue_current_idx == idx:
                self.queue_current_idx = None
                self._actions.put(("run_from", idx + 1))

    def _ensure_cookies(self, item, domain):
        account_id = item.get("account_id") or self.config.default_account_id
        if not account_id:
            self._log_item(item, f"No Kick account connected for {self._drop_title_for_item(item)}")
            self._status = "Account required"
            self._emit_state()
            return False
        if os.path.exists(cookie_file_for_account(domain, account_id)) or os.path.exists(cookie_file_for_account(domain, None)):
            return True
        self._log_item(item, f"No valid cookies for {self._account_name(account_id)}; login required")
        self._status = "Login required"
        self._emit_state()
        return False

    def _switch_to_live_alternative(self, idx):
        item = self.config.items[idx]
        current_url = item.get("url", "")
        channels = item.get("campaign_channels", [])
        tried = item.get("tried_channels", [])
        if current_url and current_url not in tried:
            tried.append(current_url)
        all_urls = []
        for channel in channels:
            url = channel.get("url") if isinstance(channel, dict) else channel
            if url:
                all_urls.append(url)
        if current_url and current_url not in all_urls:
            all_urls.append(current_url)
        if all_urls and len(tried) >= len(all_urls):
            tried.clear()
        for channel in channels:
            alt_url = channel.get("url") if isinstance(channel, dict) else channel
            if alt_url and alt_url != current_url and alt_url not in tried and kick_is_live_by_api(alt_url):
                item["url"] = alt_url
                tried.append(alt_url)
                item["tried_channels"] = tried
                self.config.save()
                self._log_item(item, f"Streamer went offline, moving to next: {self._streamer_name_from_url(alt_url)}", alt_url)
                return True
        item["tried_channels"] = tried
        self.config.save()
        return False

    def _complete_manual_skip(self, idx):
        item = self.config.items[idx]
        next_url = item.pop("_manual_next_url", None)
        skip_drop = bool(item.pop("_manual_skip_drop", False))
        if not next_url and not skip_drop:
            return False
        self.workers.pop(idx, None)
        if next_url:
            item["url"] = next_url
            self.config.save()
            if self.queue_running:
                self._actions.put(("start_index", idx))
            self._emit_state()
            return True
        self.config.save()
        if self.queue_running:
            self.queue_current_idx = None
            self._actions.put(("run_from", idx + 1))
        self._emit_state()
        return True

    def _save_worker_progress(self, idx, reason=None):
        worker = self.workers.get(idx)
        if not worker or idx < 0 or idx >= len(self.config.items):
            return 0
        elapsed = int(getattr(worker, "elapsed_seconds", 0) or 0)
        if elapsed <= 0:
            return 0
        item = self.config.items[idx]
        if item.get("is_global_drop"):
            item["cumulative_time"] = int(item.get("cumulative_time", 0) or 0) + elapsed
            item["watched_seconds"] = item["cumulative_time"]
        else:
            item["watched_seconds"] = int(item.get("watched_seconds", 0) or 0) + elapsed
        self.config.save()
        suffix = f" ({reason})" if reason else ""
        self._log_item(item, f"Saved progress{suffix}: {self._progress_text_for_seconds(item, 0)}")
        return elapsed

    def _sync_claimed(self, campaign_id, account_id):
        driver = None
        try:
            claim_result = claim_available_drops(account_id=account_id)
            claim_driver = claim_result.get("driver")
            if claim_driver:
                claim_driver.quit()
            result = fetch_drops_progress(account_id=account_id)
            driver = result.get("driver")
            progress_data = result.get("progress", [])
            match = next((c for c in progress_data if isinstance(c, dict) and c.get("id") == campaign_id), None)
            if not match:
                return
            rewards = match.get("rewards", [])
            all_claimed = bool(rewards) and all(bool(r.get("claimed")) for r in rewards if isinstance(r, dict))
            claimed = match.get("status") == "claimed" or all_claimed
            with self._lock:
                idx = self._find_campaign_index(campaign_id)
                if idx is None:
                    return
                item = self.config.items[idx]
                if claimed:
                    item["claimed"] = True
                    item["finished"] = True
                    self._log_item(item, f"Drop {self._drop_title_for_item(item)} claimed")
                    if not self.queue_running and not self.workers:
                        self.config.remove(idx)
                    else:
                        self.config.save()
                else:
                    item["progress_units"] = match.get("progress_units", 0)
                    self.config.save()
                    self._log_item(item, f"Drop {self._drop_title_for_item(item)} is on {self._progress_text_for_seconds(item, 0)}")
                self._emit_state()
        except Exception as exc:
            debug_print(f"DEBUG: Could not sync claimed drop state: {exc}")
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _offline_retry_loop(self):
        while self._running:
            time.sleep(30)
            try:
                with self._lock:
                    if not self.queue_running or self.workers:
                        continue
                    items = list(enumerate(self.config.items))
                for idx, item in items:
                    if item.get("finished") or item.get("claimed"):
                        continue
                    if kick_is_live_by_api(item["url"]):
                        self._log_item(item, f"Stream back online, retrying: {self._streamer_name_from_url(item['url'])}")
                        self._actions.put(("start_index", idx))
                        break
            except Exception as exc:
                debug_print(f"DEBUG: Offline retry monitor error: {exc}")

    def _emit_state(self):
        self.emit({"type": "state", "state": self.state()})

    def _accept_cookies_later(self, driver):
        time.sleep(1.5)
        try:
            accept_kick_cookies(driver)
        except Exception:
            pass

    def _detect_logged_in_kick_name(self, driver):
        endpoints = (
            "https://kick.com/api/v2/user",
            "https://kick.com/api/v1/user",
            "https://web.kick.com/api/v1/user",
            "https://web.kick.com/api/v1/users/me",
        )
        script = """
        const cb = arguments[arguments.length - 1];
        const urls = arguments[0];
        (async () => {
          for (const url of urls) {
            try {
              const res = await fetch(url, {
                credentials: 'include',
                cache: 'no-store',
                headers: { 'Accept': 'application/json' }
              });
              if (!res.ok) continue;
              const text = await res.text();
              if (text) return cb(text);
            } catch (e) {}
          }
          cb('');
        })();
        """
        try:
            driver.set_script_timeout(15)
            text = driver.execute_async_script(script, list(endpoints))
            data = json.loads(text) if text else None
        except Exception:
            data = None
        candidates = []
        if isinstance(data, dict):
            candidates.append(data)
            for key in ("data", "user", "result"):
                if isinstance(data.get(key), dict):
                    candidates.append(data[key])
        for candidate in candidates:
            for key in ("username", "name", "slug", "display_name"):
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _log(self, message, drop=None, creator=None):
        record = {
            "time": time.strftime("%H:%M:%S"),
            "message": message,
            "drop": drop or "",
            "creator": creator or "",
        }
        self.emit({"type": "log", "log": record})

    def _log_item(self, item, message, creator_url=None):
        self._log(
            message,
            self._drop_title_for_item(item),
            self._streamer_name_from_url(creator_url or item.get("url", "")),
        )

    def _maybe_log_progress(self, idx):
        now = time.monotonic()
        last = self._last_progress_log.get(idx, 0)
        if now - last < 300:
            return
        item = self.config.items[idx]
        self._last_progress_log[idx] = now
        self._log_item(item, f"Drop {self._drop_title_for_item(item)} is on {self._progress_text_for_seconds(item, 0)}")

    def _serialize_item(self, idx, item):
        worker = self.workers.get(idx)
        elapsed = int(getattr(worker, "elapsed_seconds", 0) or 0)
        url = item.get("url", "")
        return {
            "index": idx,
            "drop": self._drop_title_for_item(item),
            "creator": self._streamer_name_from_url(url),
            "url": url,
            "account_id": item.get("account_id") or "",
            "account": self._account_name(item.get("account_id")),
            "target_seconds": int(item.get("minutes", 0) or 0) * 60,
            "watched_seconds": int(item.get("cumulative_time" if item.get("is_global_drop") else "watched_seconds", 0) or 0) + elapsed,
            "progress": self._progress_text_for_seconds(item, elapsed),
            "status": "Watching" if worker else ("Finished" if item.get("finished") else "Queued"),
            "is_manual_link": bool(item.get("is_manual_link")),
        }

    def _serialize_campaign(self, campaign):
        rewards = []
        reward_image = ""
        for reward in campaign.get("rewards", []):
            name = reward.get("name")
            if name:
                rewards.append(name)
            if not reward_image:
                reward_image = (
                    reward.get("image_url")
                    or reward.get("image")
                    or reward.get("icon_url")
                    or reward.get("reward_image")
                    or ""
                )
        channels = self._campaign_channel_payload(campaign)
        return {
            "id": campaign.get("id") or "",
            "name": campaign.get("name", "Unknown Campaign"),
            "game": campaign.get("game", "Unknown Game"),
            "game_image": campaign.get("game_image", ""),
            "reward_image": reward_image,
            "status": campaign.get("progress_status", campaign.get("status", "unknown")),
            "rewards": ", ".join(rewards[:4]),
            "channels": ", ".join(
                (channel.get("username") or self._streamer_name_from_url(channel.get("url", "")))
                for channel in channels[:6]
            ),
            "minutes": self._campaign_minutes(campaign),
            "time": f"{self._campaign_minutes(campaign)} Minutes",
            "raw": campaign,
        }

    def _load_cached_campaigns(self):
        try:
            if not os.path.exists(DROPS_CACHE_FILE):
                return []
            with open(DROPS_CACHE_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, dict):
                campaigns = data.get("campaigns", [])
                return campaigns if isinstance(campaigns, list) else []
        except Exception:
            pass
        return []

    def _save_cached_campaigns(self, campaigns):
        try:
            os.makedirs(os.path.dirname(DROPS_CACHE_FILE), exist_ok=True)
            with open(DROPS_CACHE_FILE, "w", encoding="utf-8") as file:
                json.dump({"saved_at": time.time(), "campaigns": campaigns}, file)
        except Exception as exc:
            debug_print(f"DEBUG: Could not save WinUI drops cache: {exc}")

    def _add_or_update_campaign(self, campaign, account_id):
        campaign_id = campaign.get("id")
        existing_idx = self._find_campaign_index(campaign_id)
        channels = self._campaign_channel_payload(campaign)
        selected_url = channels[0]["url"] if channels else None
        if not selected_url:
            return False
        values = {
            "url": selected_url,
            "minutes": self._campaign_minutes(campaign),
            "campaign_id": campaign_id,
            "campaign_channels": channels,
            "required_category_id": self._campaign_category_id(campaign),
            "is_global_drop": True,
            "campaign_name": campaign.get("name"),
            "game": campaign.get("game"),
            "reward_names": self._campaign_reward_names(campaign),
            "progress_units": campaign.get("progress_units", 0),
            "claimed": campaign.get("progress_status") == "claimed",
            "account_id": account_id,
            "is_manual_link": False,
            "drop_name": None,
        }
        if existing_idx is not None:
            self.config.items[existing_idx].update(values)
            self.config.save()
            return True
        self.config.add(**values)
        return True

    def _campaign_channel_payload(self, campaign):
        return [
            {
                "url": channel.get("url") if isinstance(channel, dict) else channel,
                "username": channel.get("username", "") if isinstance(channel, dict) else "",
            }
            for channel in campaign.get("channels", [])
            if (channel.get("url") if isinstance(channel, dict) else channel)
        ]

    def _campaign_minutes(self, campaign, default=120):
        minutes = default
        for reward in campaign.get("rewards", []):
            required = reward.get("required_units", 0)
            if required > minutes:
                minutes = required
        return minutes

    def _campaign_category_id(self, campaign):
        category = campaign.get("category", {})
        if isinstance(category, dict) and category.get("id"):
            return category.get("id")
        progress_data = campaign.get("progress_data", {})
        if isinstance(progress_data, dict):
            progress_category = progress_data.get("category", {})
            if isinstance(progress_category, dict):
                return progress_category.get("id")
        return campaign.get("category_id")

    def _campaign_reward_names(self, campaign):
        return [
            reward.get("name")
            for reward in campaign.get("rewards", [])
            if reward.get("name")
        ]

    def _progress_text_for_seconds(self, item, elapsed_seconds):
        base = item.get("cumulative_time", 0) if item.get("is_global_drop") else item.get("watched_seconds", 0)
        total = int(base or 0) + int(elapsed_seconds or 0)
        target_minutes = int(item.get("minutes", 0) or 0)
        if not target_minutes:
            return f"Manual | {self._format_duration(total)}"
        target = target_minutes * 60
        remaining = max(0, target - total)
        return f"{self._format_duration(total)} / {self._format_duration(target)} | {self._format_duration(remaining)} left"

    def _format_duration(self, seconds):
        seconds = max(0, int(seconds or 0))
        minutes, secs = divmod(seconds, 60)
        return f"{minutes}m {secs}s" if minutes else f"{secs}s"

    def _drop_title_for_item(self, item):
        if item.get("campaign_name"):
            parts = [item["campaign_name"]]
            if item.get("game"):
                parts.append(item["game"])
            rewards = item.get("reward_names") or []
            if rewards:
                parts.append(", ".join(rewards[:3]))
            if item.get("account_id"):
                parts.append(self._account_name(item.get("account_id")))
            return " | ".join(parts)
        if item.get("drop_name"):
            return item.get("drop_name")
        if item.get("is_manual_link"):
            return "Manual link"
        return item.get("url", "")

    def _streamer_name_from_url(self, url):
        try:
            parsed = urlparse(url)
            return parsed.path.strip("/").split("/")[0] or url
        except Exception:
            return url

    def _account_name(self, account_id):
        if not account_id:
            return "No account"
        for account in self.config.accounts:
            if account.get("id") == account_id:
                return account.get("name", account_id)
        return account_id

    def _cookies_valid(self, account_id):
        return bool(account_id and os.path.exists(cookie_file_for_account("kick.com", account_id)))

    def _find_campaign_index(self, campaign_id):
        if not campaign_id:
            return None
        for idx, item in enumerate(self.config.items):
            if item.get("campaign_id") == campaign_id:
                return idx
        return None

    def _prune_claimed_items(self):
        before = len(self.config.items)
        self.config.items = [item for item in self.config.items if not item.get("claimed")]
        if len(self.config.items) != before:
            self.config.save()

    def _reindex_workers(self):
        self.workers = {
            new_idx: self.workers[old_idx]
            for new_idx, old_idx in enumerate(sorted(self.workers.keys()))
            if old_idx < len(self.config.items)
        }
