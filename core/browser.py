"""Browser automation and cookie management"""
import json
import os
import re
import shutil
import subprocess
import undetected_chromedriver as uc
from utils.helpers import cookie_file_for_domain, CHROME_DATA_DIR


class CookieManager:
    """Manages browser cookies for authentication"""
    
    @staticmethod
    def save_cookies(driver, domain):
        """Save cookies from driver to file"""
        path = cookie_file_for_domain(domain)
        cookies = driver.get_cookies()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        return path

    @staticmethod
    def load_cookies(driver, domain):
        """Load cookies from file into driver"""
        path = cookie_file_for_domain(domain)
        if not os.path.exists(path):
            return False
        with open(path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        for c in cookies:
            # Fix certain fields that cause problems
            if "expiry" in c and c["expiry"] is None:
                del c["expiry"]
            try:
                driver.add_cookie(c)
            except Exception:
                pass
        return True

    @staticmethod
    def import_from_browser(domain: str) -> bool:
        """Attempts to import existing cookies from browsers (Chrome/Edge/Firefox)
        using browser_cookie3. Returns True if a file was written.
        """
        try:
            import browser_cookie3 as bc3  # type: ignore
        except Exception:
            return False

        try:
            cj = bc3.load(domain_name=domain)
        except Exception:
            cj = None

        if not cj:
            return False

        cookies = []
        try:
            for c in cj:
                if not getattr(c, "name", None):
                    continue
                cookie = {
                    "name": c.name,
                    "value": c.value,
                    "domain": getattr(c, "domain", domain) or domain,
                    "path": getattr(c, "path", "/") or "/",
                    "secure": bool(getattr(c, "secure", False)),
                }
                exp = getattr(c, "expires", None)
                if exp is not None:
                    try:
                        cookie["expiry"] = int(exp)
                    except Exception:
                        pass
                cookies.append(cookie)
        except Exception:
            return False

        if not cookies:
            return False

        path = cookie_file_for_domain(domain)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=2)
            return True
        except Exception:
            return False


def _chrome_executable_candidates():
    """Yield likely Chrome executables in preference order."""
    seen = set()
    candidates = []

    if os.name == "nt":
        for env_name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(env_name)
            if base:
                candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))

    for command in ("chrome", "google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(command)
        if path:
            candidates.append(path)

    for path in candidates:
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized in seen:
            continue
        seen.add(normalized)
        if os.path.exists(path):
            yield path


def _parse_major_version(version_text):
    match = re.search(r"(\d+)\.", version_text or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _chrome_version_from_registry():
    if os.name != "nt":
        return None

    try:
        import winreg
    except Exception:
        return None

    keys = (
        (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Google\Chrome\BLBeacon"),
    )
    for root, key_name in keys:
        try:
            with winreg.OpenKey(root, key_name) as key:
                version, _ = winreg.QueryValueEx(key, "version")
                major = _parse_major_version(str(version))
                if major:
                    return major
        except Exception:
            continue
    return None


def _chrome_version_from_executable(path):
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=flags,
        )
    except Exception:
        return None

    return _parse_major_version((proc.stdout or "") + " " + (proc.stderr or ""))


def _detect_chrome():
    """Return (major_version, executable_path) for installed Chrome when possible."""
    executable = next(_chrome_executable_candidates(), None)

    if os.name == "nt":
        major = _chrome_version_from_registry()
        if major:
            return major, executable

    for path in ([executable] if executable else []):
        major = _chrome_version_from_executable(path)
        if major:
            return major, path

    return None, executable


def make_chrome_driver(
    headless=True,
    visible_width=1280,
    visible_height=800,
    driver_path=None,
    extension_path=None,
):
    """Create and configure a Chrome driver instance"""
    opts = uc.ChromeOptions()  # Use undetected-chromedriver options

    # Headless configuration (adapted for uc)
    if headless:
        try:
            opts.add_argument("--headless=new")
        except Exception:
            opts.add_argument("--headless")
        opts.add_argument("--disable-gpu")
    else:
        opts.add_argument(f"--window-size={visible_width},{visible_height}")

    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    # Remove redundant experimental options to avoid parsing error
    # (undetected-chromedriver already handles this natively)
    opts.add_argument("--log-level=3")
    opts.add_argument("--silent")

    user_data_dir = CHROME_DATA_DIR
    os.makedirs(user_data_dir, exist_ok=True)
    opts.add_argument(f"--user-data-dir={user_data_dir}")

    # Extension loading (compatible with uc)
    if extension_path:
        try:
            if extension_path.lower().endswith(".crx"):
                opts.add_extension(extension_path)
            else:
                opts.add_argument(f"--load-extension={extension_path}")
        except Exception:
            pass

    chrome_major, chrome_executable = _detect_chrome()
    driver_kwargs = {
        "options": opts,
        "version_main": chrome_major,
    }
    if chrome_executable:
        driver_kwargs["browser_executable_path"] = chrome_executable
    if driver_path and os.path.isfile(driver_path):
        driver_kwargs["driver_executable_path"] = driver_path

    driver = uc.Chrome(**driver_kwargs)

    return driver

