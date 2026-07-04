#!/usr/bin/env python3
"""
super.imyaigc.com Auto Sign-In Script
Features: cookie persistence, proxy, config, logging, history, password encryption,
          CLI args, auto-cleanup, anti-detection
"""

import asyncio
import argparse
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from imyai_network import auto_proxy_urls, config_proxy_url, urlopen_auto

# --- Password Encryption ---
try:
    import keyring
    HAS_KEYRING = True
except ImportError:
    HAS_KEYRING = False

try:
    from cryptography.fernet import Fernet
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

SERVICE_NAME = "super-imyaigc-signin"
KEY_FILE = Path(__file__).parent / ".secret_key"


def get_or_create_key():
    if not HAS_CRYPTO:
        return None
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    return key


def encrypt_password(password: str) -> str:
    if not HAS_CRYPTO:
        return password
    return Fernet(get_or_create_key()).encrypt(password.encode()).decode()


def decrypt_password(encrypted: str) -> str:
    if not HAS_CRYPTO:
        return encrypted
    if not encrypted:
        return ""
    try:
        return Fernet(get_or_create_key()).decrypt(encrypted.encode()).decode()
    except Exception:
        return encrypted


# --- Logging ---
def setup_logging(log_file: str, level=logging.INFO):
    logger = logging.getLogger("signin")
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


# --- Config ---
def load_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_config(config: dict, config_path: str):
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


# --- Anti-Detection ---
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720},
    {"width": 1600, "height": 900},
]

STEALTH_JS = """
() => {
    // Remove webdriver flag
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    
    // Override plugins to look real
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client', filename: 'internal-nacl-plugin' },
        ]
    });
    
    // Override languages
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
    
    // Override permissions
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
    );
    
    // Chrome runtime
    window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
    
    // Remove automation-related properties
    delete navigator.__proto__.webdriver;
    
    // Override getOwnPropertyDescriptor for iframe detection
    const originalGetOwnPropertyDescriptor = Object.getOwnPropertyDescriptor;
    Object.getOwnPropertyDescriptor = function(obj, prop) {
        if (prop === 'webdriver' && obj === navigator) {
            return undefined;
        }
        return originalGetOwnPropertyDescriptor(obj, prop);
    };
}
"""


def random_delay(min_s=0.5, max_s=2.0):
    """Human-like random delay."""
    time.sleep(random.uniform(min_s, max_s))


def human_type_delay():
    """Random delay between keystrokes (50-150ms)."""
    return random.uniform(0.05, 0.15)


async def human_move_and_click(page, locator, logger=None):
    """Move mouse naturally to element then click."""
    box = await locator.bounding_box()
    if box:
        # Add random offset within the element
        x = box["x"] + random.uniform(box["width"] * 0.2, box["width"] * 0.8)
        y = box["y"] + random.uniform(box["height"] * 0.2, box["height"] * 0.8)
        # Move mouse in steps
        steps = random.randint(3, 6)
        await page.mouse.move(x, y, steps=steps)
        random_delay(0.1, 0.3)
        await page.mouse.click(x, y)
    else:
        await locator.click()


async def human_type(page, locator, text, logger=None):
    """Type text with human-like delays between characters."""
    await locator.click()
    random_delay(0.1, 0.3)
    await locator.fill("")
    for char in text:
        await locator.type(char, delay=0)
        await asyncio.sleep(human_type_delay())


async def human_scroll(page, direction="down", amount=None):
    """Scroll like a human."""
    if amount is None:
        amount = random.randint(100, 400)
    if direction == "up":
        amount = -amount
    await page.mouse.wheel(0, amount)
    random_delay(0.3, 0.8)


# --- Screenshot Cleanup ---
def cleanup_old_screenshots(screenshot_dir: str, logger):
    today = datetime.now().strftime("%Y%m%d")
    deleted = 0
    if not os.path.exists(screenshot_dir):
        return deleted
    for f in os.listdir(screenshot_dir):
        if not f.endswith(".png"):
            continue
        match = re.search(r"(\d{8})_", f)
        if match and match.group(1) < today:
            os.remove(os.path.join(screenshot_dir, f))
            deleted += 1
    if deleted > 0:
        logger.info(f"  Cleaned up {deleted} old screenshot(s)")
    return deleted


# --- Cookie Persistence ---
async def save_cookies(context, cookie_dir: str, phone: str):
    os.makedirs(cookie_dir, exist_ok=True)
    cookies = await context.cookies()
    with open(os.path.join(cookie_dir, f"{phone}.json"), "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False)


async def load_cookies(context, cookie_dir: str, phone: str) -> bool:
    cookie_file = os.path.join(cookie_dir, f"{phone}.json")
    if os.path.exists(cookie_file):
        with open(cookie_file, "r", encoding="utf-8") as f:
            await context.add_cookies(json.load(f))
        return True
    return False


# --- History ---
def record_history(history_file: str, phone: str, username: str, success: bool, screenshot: str = ""):
    os.makedirs(os.path.dirname(history_file), exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "SUCCESS" if success else "FAILED"
    with open(history_file, "a", encoding="utf-8") as f:
        f.write(f"{ts} | {phone} | {username} | {status} | {screenshot}\n")


def signin_state_path(paths: dict) -> str:
    configured = paths.get("signin_state_file") if isinstance(paths, dict) else ""
    if configured:
        return configured
    return os.path.join(paths["cookie_dir"], "signin-state.json")


def load_signin_state(paths: dict) -> dict:
    path = signin_state_path(paths)
    if not os.path.exists(path):
        return {"accounts": {}}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"accounts": {}}
    except Exception:
        return {"accounts": {}}


def save_signin_state(paths: dict, state: dict):
    path = signin_state_path(paths)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def update_signin_state(
    paths: dict,
    *,
    phone: str,
    username: str,
    success: bool,
    screenshot: str,
    streak_days: int | None,
):
    state = load_signin_state(paths)
    accounts = state.setdefault("accounts", {})
    accounts[phone] = {
        "date": today_key(),
        "success": bool(success),
        "username": username,
        "screenshot": screenshot,
        "streakDays": streak_days,
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_signin_state(paths, state)


def cached_success_for_today(state: dict, phone: str) -> dict | None:
    account_state = (state.get("accounts") or {}).get(phone)
    if not isinstance(account_state, dict):
        return None
    if account_state.get("date") != today_key() or account_state.get("success") is not True:
        return None
    return account_state


def show_history(history_file: str, logger):
    if not os.path.exists(history_file):
        logger.info("No history found.")
        return
    with open(history_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    if not lines:
        logger.info("History is empty.")
        return
    logger.info("=== Check-in History ===")
    for line in lines[-20:]:
        logger.info(line.strip())


# --- Core ---
CHAT_URL = "https://super.imyaigc.com/chat"
LOGIN_URLS = [
    CHAT_URL,
    "https://super.imyaigc.com/",
    "https://super.imyaigc.com",
]
MODEL_LIST_URL = "https://api.daka.today/api/models/list"


def find_system_browser() -> str:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return ""


def build_launch_args(headless: bool) -> dict:
    launch_args = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
        ]
    }
    browser_path = find_system_browser()
    if browser_path:
        launch_args["executable_path"] = browser_path
    return launch_args


def read_saved_cookie_header(cookie_dir: str, phone: str) -> str:
    cookie_file = os.path.join(cookie_dir, f"{phone}.json")
    if not os.path.exists(cookie_file):
        return ""
    with open(cookie_file, "r", encoding="utf-8") as f:
        cookies = json.load(f)
    parts = []
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if name and value:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def is_enabled_model(model: dict) -> bool:
    try:
        return int(model.get("status") or 0) == 1
    except Exception:
        return False


def fetch_model_count(cookie_dir: str, phone: str, network_config: dict | None = None) -> dict:
    import urllib.request

    cookie_header = read_saved_cookie_header(cookie_dir, phone)
    if not cookie_header:
        raise RuntimeError(f"No saved cookies found for {phone}; log in first")

    request = urllib.request.Request(
        MODEL_LIST_URL,
        headers={
            "Cookie": cookie_header,
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://super.imyaigc.com",
            "Referer": CHAT_URL,
        },
    )
    with urlopen_auto(request, timeout=30, config=network_config) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if payload.get("code") != 200 or payload.get("success") is not True:
        raise RuntimeError(f"Model list request failed: {payload.get('message') or payload.get('code')}")

    models = payload.get("data")
    if not isinstance(models, list):
        raise RuntimeError("Model list response did not contain a model array")

    enabled_models = [model for model in models if isinstance(model, dict) and is_enabled_model(model)]
    return {
        "total": len(models),
        "enabled": len(enabled_models),
        "disabled": len(models) - len(enabled_models),
    }


def select_playwright_proxy(config: dict) -> dict | None:
    configured = config_proxy_url(config)
    proxy_config = config.get("proxy") or {}
    auto_detect = bool(proxy_config.get("auto_detect"))
    proxy_url = configured or (next(iter(auto_proxy_urls()), "") if auto_detect else "")
    if not proxy_url:
        return None
    proxy = {"server": proxy_url}
    if configured and proxy_config.get("username"):
        proxy["username"] = proxy_config["username"]
        proxy["password"] = proxy_config.get("password", "")
    return proxy


async def goto_imyai_entry(page, timeout: int, logger) -> str:
    errors: list[str] = []
    for url in LOGIN_URLS:
        try:
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            logger.info(f"    Opened IMYAI entry: {url}")
            return url
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            logger.warning(f"    Failed IMYAI entry {url}: {exc}")
    raise RuntimeError("All IMYAI entry URLs failed: " + " | ".join(errors))


async def safe_call(func, *args, retries=3, delay=2, logger=None, **kwargs):
    for attempt in range(1, retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if attempt < retries:
                if logger:
                    logger.warning(f"    Retry {attempt}/{retries}: {e}")
                await asyncio.sleep(delay * random.uniform(0.8, 1.5))
            else:
                raise


async def close_notice(page, logger):
    try:
        close_btn = page.locator('.noticeDialog svg').first
        if await close_btn.is_visible(timeout=3000):
            random_delay(0.5, 1.0)
            await human_move_and_click(page, close_btn, logger)
            random_delay(0.8, 1.5)
            logger.info("    Closed announcement")
    except:
        pass


async def open_phone_login(page, logger):
    avatar = page.locator('.avatar-container').first
    random_delay(0.5, 1.0)
    await human_move_and_click(page, avatar, logger)
    random_delay(1.0, 2.0)
    phone_login = page.locator('text=手机号登录').first
    await human_move_and_click(page, phone_login, logger)
    random_delay(1.0, 1.5)
    logger.info("    Opened phone login form")


async def get_username(page):
    try:
        avatar = page.locator('.avatar-container').first
        await human_move_and_click(page, avatar)
        random_delay(1.5, 2.5)
        username = await page.evaluate('''() => {
            const popovers = document.querySelectorAll('[class*="popover"], [class*="n-popover"]');
            for (let p of popovers) {
                if (p.getBoundingClientRect().width > 0) {
                    const lines = p.innerText.trim().split('\\n');
                    if (lines.length > 0 && lines[0].length > 0 && lines[0].length < 30)
                        return lines[0].trim();
                }
            }
            return null;
        }''')
        random_delay(0.3, 0.6)
        await page.mouse.click(100, 300)
        random_delay(0.5, 1.0)
        return username
    except:
        return None


async def do_login(page, account, logger):
    phone_input = page.locator('input[placeholder*="手机"]').first
    password_input = page.locator('input[placeholder*="密码"]').first
    await phone_input.wait_for(state="visible", timeout=10000)
    
    # Human-like typing
    await human_type(page, phone_input, account["phone"], logger)
    random_delay(0.3, 0.8)
    await human_type(page, password_input, account["password"], logger)
    logger.info("    Entered credentials")
    
    random_delay(0.5, 1.0)
    login_btn = page.locator('button:has-text("登录")').first
    await human_move_and_click(page, login_btn, logger)
    logger.info("    Clicked login button")
    await asyncio.sleep(random.uniform(4.0, 6.0))
    
    try:
        if await phone_input.is_visible(timeout=2000):
            raise Exception("Login modal still visible")
    except:
        pass
    logger.info("    Login successful!")


async def do_checkin(page, logger):
    await dismiss_blocking_overlays(page, logger)
    avatar = page.locator('.avatar-container').first
    random_delay(0.5, 1.0)
    await human_move_and_click(page, avatar, logger)
    random_delay(1.0, 2.0)
    btn = page.locator('span:has-text("签到领积分")').first
    await btn.wait_for(state="visible", timeout=5000)
    await human_move_and_click(page, btn, logger)
    logger.info("    Clicked check-in button (opened calendar)")
    await asyncio.sleep(random.uniform(1.5, 2.5))
    # Click the actual sign-in button inside the calendar modal
    try:
        signin_btn = page.locator('button:has-text("今日尚未签到")').first
        if await signin_btn.count() > 0:
            await human_move_and_click(page, signin_btn, logger)
            logger.info("    Clicked today sign-in button")
            await asyncio.sleep(random.uniform(1.5, 2.5))
        else:
            logger.warning("    Today sign-in button not found, may already be signed in")
    except Exception as e:
        logger.warning(f"    Failed to click today sign-in button: {e}")
    await asyncio.sleep(random.uniform(1.0, 2.0))


async def do_checkin_v2(page, logger):
    await dismiss_blocking_overlays(page, logger)
    opened = await open_checkin_from_avatar_menu(page, logger)
    if not opened:
        opened = await open_checkin_from_reward_button(page, logger)
    if not opened:
        raise RuntimeError("Unable to open check-in calendar")

    await asyncio.sleep(random.uniform(1.5, 2.5))
    try:
        signin_btn = page.locator('button:has-text("今日尚未签到"), button:has-text("今日未签到")').first
        if await signin_btn.count() > 0:
            await human_move_and_click(page, signin_btn, logger)
            logger.info("    Clicked today sign-in button")
            await asyncio.sleep(random.uniform(1.5, 2.5))
        else:
            logger.warning("    Today sign-in button not found, may already be signed in")
    except Exception as e:
        logger.warning(f"    Failed to click today sign-in button: {e}")
    await asyncio.sleep(random.uniform(1.0, 2.0))


async def checkin_modal_visible(page) -> bool:
    for selector in (
        'text=签到奖励',
        'button:has-text("今日已成功签到")',
        'button:has-text("今日尚未签到")',
        'button:has-text("今日未签到")',
    ):
        try:
            if await page.locator(selector).first.is_visible(timeout=800):
                return True
        except Exception:
            pass
    return False


async def open_checkin_from_avatar_menu(page, logger) -> bool:
    try:
        avatar = page.locator('.avatar-container').first
        random_delay(0.5, 1.0)
        await human_move_and_click(page, avatar, logger)
        random_delay(1.0, 2.0)
        btn = page.locator('span:has-text("签到领积分")').first
        await btn.wait_for(state="visible", timeout=5000)
        await human_move_and_click(page, btn, logger)
        logger.info("    Clicked check-in button from avatar menu")
        return await checkin_modal_visible(page)
    except Exception as e:
        logger.warning(f"    Avatar menu check-in path failed: {e}")
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return False


async def open_checkin_from_reward_button(page, logger) -> bool:
    await dismiss_blocking_overlays(page, logger)
    buttons = page.locator('button.p-2.text-gray-500')
    for index in (1, 0):
        try:
            button = buttons.nth(index)
            if await button.is_visible(timeout=2000):
                await human_move_and_click(page, button, logger)
                logger.info(f"    Clicked check-in button from top icon entry index={index}")
                await asyncio.sleep(random.uniform(1.0, 2.0))
                if await checkin_modal_visible(page):
                    return True
        except Exception as e:
            logger.warning(f"    Top icon check-in path failed for index={index}: {e}")
    return False


async def dismiss_blocking_overlays(page, logger):
    """Advance or close product tours that block clicks on the page."""
    for _ in range(8):
        clicked = False
        for selector in (
            'button:has-text("Skip")',
            'button:has-text("Done")',
            'button:has-text("Next")',
            'button:has-text("一周不再提示")',
            'button:has-text("知道了")',
            'button:has-text("跳过")',
            'button:has-text("完成")',
            'button:has-text("下一步")',
        ):
            try:
                locator = page.locator(selector).first
                if await locator.is_visible(timeout=600):
                    await human_move_and_click(page, locator, logger)
                    logger.info(f"    Dismissed blocking overlay via {selector}")
                    await asyncio.sleep(random.uniform(0.4, 0.8))
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            break
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
    except Exception:
        pass


async def save_page_screenshot(page, screenshot_dir: str, prefix: str, username: str, logger) -> str:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    screenshot_path = os.path.join(screenshot_dir, f"{prefix}-{username}-{timestamp}.png")
    try:
        random_delay(0.3, 0.5)
        await page.screenshot(path=screenshot_path, full_page=True)
        logger.info(f"    Screenshot: {screenshot_path}")
        return screenshot_path
    except Exception as e:
        logger.error(f"    Screenshot failed: {e}")
        return ""


async def today_is_signed_in(page) -> bool:
    today_str = datetime.now().strftime("%Y-%m-%d")
    return await page.evaluate(
        """(today) => {
            const bodyText = document.body ? document.body.innerText || '' : '';
            if (bodyText.includes('今日已成功签到')) return true;

            const cell = document.querySelector(`div.n-calendar-date__date[title="${today}"]`);
            if (!cell) return false;

            const dateRoot = cell.closest('.n-calendar-date') ||
                cell.closest('[class*="calendar-date"]') ||
                cell.parentElement;
            if (!dateRoot) return false;

            const signedIcon = dateRoot.querySelector(
                'img.sign-in-signed-icon, img[alt="已签到"], img[alt*="签到"]'
            );
            if (signedIcon) return true;

            const text = dateRoot.innerText || '';
            if (text.includes('已签到')) return true;

            const gridCell = cell.closest('[role="gridcell"], td, .n-calendar-cell');
            return !!gridCell && (gridCell.innerText || '').includes('已签到');
        }""",
        today_str,
    )


async def extract_streak_days(page) -> int | None:
    text = await page.evaluate("document.body ? document.body.innerText || '' : ''")
    for pattern in (
        r"已连续签到\s*(\d+)\s*天",
        r"连续签到\s*(\d+)\s*天",
    ):
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


async def process_account(browser, account, config, logger, login_only=False):
    import datetime
    phone = account["phone"]
    paths = config["paths"]
    settings = config["settings"]
    logger.info(f"--- Account: {phone} ---")

    # Random viewport for each account
    viewport = random.choice(VIEWPORTS)
    user_agent = random.choice(USER_AGENTS)
    
    context = await browser.new_context(
        viewport=viewport,
        user_agent=user_agent,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        color_scheme="light",
    )
    
    # Inject stealth script on every page load
    await context.add_init_script(STEALTH_JS)
    
    page = await context.new_page()

    try:
        cookie_loaded = await load_cookies(context, paths["cookie_dir"], phone)
        if cookie_loaded:
            logger.info("    Loaded saved cookies")

        random_delay(0.5, 1.0)
        await goto_imyai_entry(page, settings["timeout"], logger)
        await asyncio.sleep(random.uniform(4.0, 6.0))

        # Random scroll like a human
        await human_scroll(page, "down", random.randint(50, 200))
        random_delay(0.5, 1.0)

        # Check if already logged in
        already_logged_in = False
        try:
            username = await get_username(page)
            if username and username != phone[-4:]:
                already_logged_in = True
                logger.info(f"    Already logged in as: {username}")
        except:
            pass

        if not already_logged_in:
            await safe_call(close_notice, page, logger, retries=settings["max_retries"], delay=settings["retry_delay"])
            random_delay(1.0, 2.0)
            await safe_call(open_phone_login, page, logger, retries=settings["max_retries"], delay=settings["retry_delay"])
            await safe_call(do_login, page, account, logger, retries=settings["max_retries"], delay=settings["retry_delay"])
            await save_cookies(context, paths["cookie_dir"], phone)
            logger.info("    Saved cookies")
            random_delay(1.0, 2.0)
            username = await get_username(page) or phone[-4:]
        else:
            username = await get_username(page) or phone[-4:]

        logger.info(f"    Username: {username}")

        screenshot_path = await save_page_screenshot(
            page, paths["screenshot_dir"], "pre-signin", username, logger
        )

        if login_only:
            logger.info("    Login-only mode: skipping check-in")
            record_history(paths["history_file"], phone, username, True, screenshot_path)
            return True

        try:
            await safe_call(do_checkin_v2, page, logger, retries=settings["max_retries"], delay=settings["retry_delay"])
        except Exception as e:
            logger.warning(f"    Check-in failed: {e}")

        post_screenshot_path = await save_page_screenshot(
            page, paths["screenshot_dir"], "post-signin", username, logger
        )
        if post_screenshot_path:
            screenshot_path = post_screenshot_path

        success = False
        streak_days = None
        try:
            # Check for the signed-in icon element
            signed_icon = page.locator('img.sign-in-signed-icon[alt="已签到"]')
            if await signed_icon.count() > 0:
                # Get today's date number
                today_str = datetime.datetime.now().strftime('%Y-%m-%d')
                # Check if today's calendar cell has signed icon nearby
                today_cell = page.locator(f'div.n-calendar-date__date[title="{today_str}"]')
                # Check if today's date cell also exists
                today_cell = page.locator('div.n-calendar-date__date[title="' + today_str + '"]')
                if await today_cell.count() > 0:
                    success = True
                    logger.info("    Confirmed: sign-in icon found for today")
                else:
                    logger.warning("    Today cell not found, but signed icon exists")
            else:
                logger.warning("    No sign-in icon found on page")
        except Exception as e:
            logger.warning(f'    Confirmation check failed: {e}')

        try:
            strict_success = await today_is_signed_in(page)
            if success and not strict_success:
                logger.warning("    Previous broad check was ignored: today's cell is not signed in")
            success = strict_success
            if success:
                logger.info("    Strict confirmation passed for today's calendar cell")
            else:
                logger.warning("    Strict confirmation failed for today's calendar cell")
        except Exception as e:
            success = False
            logger.warning(f"    Strict confirmation check failed: {e}")

        try:
            streak_days = await extract_streak_days(page)
            if streak_days is not None:
                logger.info(f"    Consecutive sign-in days: {streak_days}")
            else:
                logger.warning("    Consecutive sign-in days: unavailable")
        except Exception as e:
            logger.warning(f"    Consecutive sign-in days check failed: {e}")

        record_history(paths["history_file"], phone, username, success, screenshot_path)
        update_signin_state(
            paths,
            phone=phone,
            username=username,
            success=success,
            screenshot=screenshot_path,
            streak_days=streak_days,
        )
        return success

    except Exception as e:
        logger.error(f"    Error: {e}")
        ts = time.strftime("%Y%m%d_%H%M%S")
        try:
            await page.screenshot(path=os.path.join(paths["screenshot_dir"], f"error-{phone[-4:]}-{ts}.png"))
        except:
            pass
        record_history(paths["history_file"], phone, phone[-4:], False, "")
        update_signin_state(
            paths,
            phone=phone,
            username=phone[-4:],
            success=False,
            screenshot="",
            streak_days=None,
        )
        return False
    finally:
        await page.close()
        await context.close()


# --- CLI ---
def parse_args():
    parser = argparse.ArgumentParser(description="super.imyaigc.com Auto Sign-In")
    parser.add_argument("--account", "-a", type=int, help="Account index (1-based)")
    parser.add_argument("--phone", "-p", type=str, help="Account by phone number")
    parser.add_argument("--password", "-w", type=str, help="One-off password override for the selected account")
    parser.add_argument("--history", action="store_true", help="Show check-in history")
    parser.add_argument("--config", "-c", type=str, default=None, help="Config file path")
    parser.add_argument("--set-password", "-sp", nargs=2, metavar=("PHONE", "PWD"), help="Encrypt and store password")
    parser.add_argument("--headless", action="store_true", default=None, help="Headless mode")
    parser.add_argument("--no-headless", dest="headless", action="store_false", help="Show browser")
    parser.add_argument("--no-cleanup", action="store_true", help="Skip deleting old screenshots")
    parser.add_argument("--login-only", action="store_true", help="Stop after successful login and skip check-in")
    parser.add_argument("--model-count", action="store_true", help="Print supported chat model counts after login")
    parser.add_argument("--skip-success-today", action="store_true", help="Skip accounts already signed in today")
    parser.add_argument("--retries", "-r", type=int, default=3, help="Number of retries for failed sign-ins (default: 3)")
    return parser.parse_args()


async def main():
    args = parse_args()
    script_dir = Path(__file__).parent
    config_path = args.config or str(script_dir / "config.json")
    config = load_config(config_path)
    paths = config["paths"]
    logger = setup_logging(paths["log_file"])

    if args.set_password:
        phone, pwd = args.set_password
        for acc in config["accounts"]:
            if acc["phone"] == phone:
                acc["password"] = encrypt_password(pwd)
                save_config(config, config_path)
                logger.info(f"Password encrypted and saved for {phone}")
                break
        else:
            logger.error(f"Phone {phone} not found in config")
        return

    if args.history:
        show_history(paths["history_file"], logger)
        return

    if not args.no_cleanup:
        cleanup_old_screenshots(paths["screenshot_dir"], logger)

    for acc in config["accounts"]:
        if acc["password"] and HAS_CRYPTO:
            acc["password"] = decrypt_password(acc["password"])
        elif not acc["password"] and HAS_KEYRING:
            import keyring as kr
            acc["password"] = kr.get_password(SERVICE_NAME, acc["phone"]) or ""

    accounts = config["accounts"]
    if args.account:
        idx = args.account - 1
        if 0 <= idx < len(accounts):
            accounts = [accounts[idx]]
        else:
            logger.error(f"Account index {args.account} out of range (1-{len(config['accounts'])})")
            return
    elif args.phone:
        accounts = [a for a in accounts if a["phone"] == args.phone]
        if not accounts:
            logger.error(f"Phone {args.phone} not found")
            return

    if args.password is not None:
        if len(accounts) != 1:
            logger.error("--password requires exactly one selected account; use --phone or --account")
            return
        accounts[0]["password"] = args.password

    for acc in accounts:
        if not acc["password"]:
            logger.error(f"No password for {acc['phone']}. Use --set-password")
            return

    skipped_results = []
    if args.skip_success_today and not args.login_only and not args.model_count:
        state = load_signin_state(paths)
        pending_accounts = []
        for account in accounts:
            cached = cached_success_for_today(state, account["phone"])
            if cached:
                logger.info(f"--- Account: {account['phone']} ---")
                logger.info("    Skipped: already signed in today")
                streak_days = cached.get("streakDays")
                if streak_days is not None:
                    logger.info(f"    Consecutive sign-in days: {streak_days}")
                screenshot = cached.get("screenshot")
                if screenshot:
                    logger.info(f"    Screenshot: {screenshot}")
                skipped_results.append(
                    {
                        "account": account["phone"],
                        "success": True,
                        "skipped": True,
                        "streakDays": streak_days,
                    }
                )
            else:
                pending_accounts.append(account)
        accounts = pending_accounts

    if args.skip_success_today and not accounts and not args.model_count:
        logger.info("")
        logger.info("=== RESULTS ===")
        for r in skipped_results:
            suffix = " SKIPPED"
            streak = r.get("streakDays")
            streak_text = f" streakDays={streak}" if streak is not None else ""
            logger.info(f"  {r['account']}: OK{suffix}{streak_text}")
        return

    settings = config["settings"]
    headless = args.headless if args.headless is not None else settings.get("headless", True)
    proxy = select_playwright_proxy(config)

    from playwright.async_api import async_playwright
    os.makedirs(paths["screenshot_dir"], exist_ok=True)
    launch_args = build_launch_args(headless)
    if proxy:
        launch_args["proxy"] = proxy
        logger.info(f"Using Playwright proxy: {proxy['server']}")
    else:
        logger.info("Using direct Playwright connection")

    if args.model_count:
        missing_cookie_accounts = [
            account for account in accounts
            if not read_saved_cookie_header(paths["cookie_dir"], account["phone"])
        ]
        if missing_cookie_accounts:
            logger.info("Saved cookies missing; logging in before querying model count")
            async with async_playwright() as p:
                browser = await p.chromium.launch(**launch_args)
                for account in missing_cookie_accounts:
                    await process_account(browser, account, config, logger, login_only=True)
                await browser.close()

        logger.info("")
        logger.info("=== MODEL COUNTS ===")
        for account in accounts:
            counts = fetch_model_count(paths["cookie_dir"], account["phone"], network_config=config)
            logger.info(
                f"  {account['phone']}: supported={counts['enabled']} total={counts['total']} disabled={counts['disabled']}"
            )
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_args)
        max_retries = args.retries if hasattr(args, "retries") else 3
        retry_delay = 3  # seconds between retries
        
        results = list(skipped_results)
        for account in accounts:
            success = False
            for attempt in range(max_retries):
                success = await process_account(browser, account, config, logger, login_only=args.login_only)
                if success:
                    break
                if attempt < max_retries - 1:
                    logger.warning(f"    Retry {attempt + 1}/{max_retries} failed, waiting {retry_delay}s before next attempt...")
                    await asyncio.sleep(retry_delay)
            cached = (load_signin_state(paths).get("accounts") or {}).get(account["phone"], {})
            results.append(
                {
                    "account": account["phone"],
                    "success": success,
                    "skipped": False,
                    "streakDays": cached.get("streakDays"),
                }
            )
        await browser.close()

        logger.info("")
        logger.info("=== RESULTS ===")
        for r in results:
            suffix = " SKIPPED" if r.get("skipped") else ""
            streak = r.get("streakDays")
            streak_text = f" streakDays={streak}" if streak is not None else ""
            logger.info(f"  {r['account']}: {'OK' if r['success'] else 'FAIL'}{suffix}{streak_text}")


if __name__ == "__main__":
    asyncio.run(main())
