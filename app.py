#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, time, json, requests, subprocess
import urllib.request, urllib.parse, urllib.error
from datetime import datetime
from seleniumbase import SB

# 环境变量配置 (可以直接私库在双引号里填写)
EMAIL         = os.environ.get("EMAIL") or ""           # 邮箱,只用于通知使用，可随意填写
SESSION_TOKEN = os.environ.get("SESSION_TOKEN") or ""   # session token，默认登录方式,非必须
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN") or ""   # Discord Token 备用登录方式, 失败时才使用,必须填写
GH_TOKEN      = os.environ.get("GH_TOKEN") or ""        # GitHub PAT token,用于自动更新session token,可选
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""      # TG chat id,不填写不通知，需和bot token一起填写生效
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""    # TG bot token 

# 解析 DISCORD_TOKEN
DC_TOKEN = ""
if DISCORD_TOKEN:
    _parts = DISCORD_TOKEN.split(",", 1)
    DC_TOKEN = _parts[-1].strip()

if not SESSION_TOKEN and not DC_TOKEN:
    print("ℹ️ 未配置 SESSION_TOKEN 和 DISCORD_TOKEN,脚本终止。")
    sys.exit(1)

# 记录本次登录方式（用于通知）
_LOGIN_METHOD = "SESSION_TOKEN"

# 获取 cookie 到期时间
def get_cookie_info(sb, name):
    cookies = sb.get_cookies()
    if not cookies:
        return None, None
    for c in cookies:
        if c.get('name') == name:
            value = c.get('value')
            expiry_ts = c.get('expiry')
            expiry_dt = datetime.fromtimestamp(expiry_ts) if expiry_ts else None
            return value, expiry_dt
    return None, None

# 检查是否需要更新 cookie
def should_update_cookie(new_value, old_value, expiry_dt, days_threshold=3):
    if new_value is None:
        return False
    if new_value != old_value:
        return True
    if expiry_dt:
        remaining = (expiry_dt - datetime.now()).total_seconds()
        if remaining < days_threshold * 24 * 3600:
            return True
    return False

# 更新 cookie 到 secrets
def update_github_secret(secret_name, new_value):
    if not new_value:
        print(f"⚠️ 跳过更新 {secret_name}：新值为空")
        return False
    masked = new_value[:4] + "..." + new_value[-4:] if len(new_value) > 8 else "***"
    print(f"🔄 更新 Secret: {secret_name} (新值: {masked})")
    try:
        env = os.environ.copy()
        if GH_TOKEN:
            env["GH_TOKEN"] = GH_TOKEN
        proc = subprocess.run(
            ["gh", "secret", "set", secret_name, "--body", new_value],
            capture_output=True, text=True, timeout=30, check=False,
            env=env
        )
        if proc.returncode == 0:
            return True
        else:
            print(f"❌ 更新失败: {proc.stderr.strip()}")
            return False
    except Exception as e:
        print(f"❌ 异常: {e}")
        return False

# 发送 tg 通知
def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
        print("✅ Telegram 通知已发送")
    except Exception as e:
        print(f"❌ Telegram 发送失败: {e}")

# 通知格式
def format_notification(status: str, extra: str = "", error: str = "", expiry_date: str = "") -> str:
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    if '@' in EMAIL:
        name, domain = EMAIL.split('@', 1)
        if len(name) > 4:
            masked_email = f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            masked_email = f"{name}@{domain}"
    else:
        masked_email = EMAIL[:2] + '****' 
    
    lines = [
        "🇫🇮 Bot-hosting 续期通知",
        "",
        f"{status}",
        f"👤 登录账户: {masked_email}",
    ]
    if _LOGIN_METHOD != "SESSION_TOKEN":
        lines.append(f"🔐 登录方式: {_LOGIN_METHOD}")
    if expiry_date:
        lines.append(f"📅 到期时间: {expiry_date}")
    if extra:
        lines.append(extra)
    if error:
        lines.append(f"⚠️ 错误信息: {error}")
    lines.append(f"⏱️ 登录时间: {now}")
    return "\n".join(lines)

# 等待 Turnstile 验证通过
def wait_for_turnstile_pass(sb, timeout=30):
    start = time.time()
    cf_indicators = ["verify you are human", "确认您是真人", "troubleshoot", "just a moment"]
    while time.time() - start < timeout:
        page_lower = sb.get_page_source().lower()
        if not any(x in page_lower for x in cf_indicators):
            print("✅ Turnstile 验证已通过")
            return True
        sb.sleep(1)
    print("❌ Turnstile 验证超时未通过")
    return False
    
# 获取当前出口 ip
def get_current_ip(proxy_server: str = "") -> str:
    proxies = None
    if proxy_server:
        proxies = {"http": proxy_server, "https": proxy_server}
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()

# 时间格式化
def format_countdown(countdown_str: str) -> str:
    try:
        h, m, _ = countdown_str.split(':')
        h = int(h)
        m = int(m)
        if h > 0:
            return f"{h}h{m}min"
        else:
            return f"{m}min"
    except:
        return countdown_str

# 获取过期日期
def extract_expiry_date(page_source: str) -> str:
    patterns = [
        r"[Ee]xpires\s*[:\-]?\s*(\d{4}/\d{2}/\d{2})",
        r"[Ee]xpires\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",
        r"[Ee]xpires\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})",
        r"[Ee]xpires\s*[:\-]?\s*(\d{2}-\d{2}-\d{4})",
        r"(\d{4}/\d{2}/\d{2})\s*[\-–]\s*renew",
        r"(\d{2}/\d{2}/\d{4})\s*[\-–]\s*renew",
        r"(\d{4}-\d{2}-\d{2})\s*[\-–]\s*renew",
        r"(\d{2}-\d{2}-\d{4})\s*[\-–]\s*renew",
        r"(\d{4}/\d{2}/\d{2})\s*[\-–]\s*renew manually to extend for 4 days",
    ]
    for pattern in patterns:
        match = re.search(pattern, page_source)
        if match:
            date_str = match.group(1)
            # 把 dd/mm/yyyy 统一成 yyyy/mm/dd; dd-mm-yyyy -> yyyy-mm-dd
            sep = '-' if '-' in date_str else '/'
            parts = date_str.split(sep)
            if len(parts[-1]) == 4 and len(parts[0]) == 2:
                return f"{parts[2]}{sep}{parts[0]}{sep}{parts[1]}"
            return date_str
    return None

# ---------- 续期弹窗诊断辅助 (定位 "点了没生效" 问题) ----------
def inspect_renew_buttons(sb):
    """枚举页面上所有含 'Renew' 的按钮及其可见/禁用状态"""
    try:
        info = sb.execute_script("""
            (function(){
                return Array.from(document.querySelectorAll('button'))
                    .map(b => ({
                        text: (b.innerText || b.textContent || '').trim().slice(0, 60),
                        visible: b.offsetParent !== null,
                        disabled: !!b.disabled,
                    }))
                    .filter(x => /renew/i.test(x.text));
            })()
        """)
        if not info:
            print("🔍 页面上没有含 'Renew' 的按钮")
        for x in info:
            state = ('可见' if x['visible'] else '隐藏') + ('·禁用' if x['disabled'] else '')
            print(f"  🔍 [{state}] {x['text']}")
        return info
    except Exception as e:
        print(f"⚠️ 枚举 Renew 按钮失败: {e}")
        return []


def save_screenshot(sb, name):
    """保存截图到 /tmp, 便于人工查看弹窗结构"""
    try:
        path = f"/tmp/{name}.png"
        sb.save_screenshot(path)
        print(f"📸 截图已保存: {path}")
        return path
    except Exception as e:
        print(f"⚠️ 截图失败: {e}")
        return None


def wait_for_renew_button(sb, timeout=45):
    """轮询等待"可见且未禁用"的 'Renew for X days' 按钮出现

    关键: Turnstile 校验真正完成后该按钮才会从禁用变为可用,
    wait_for_turnstile_pass 只靠页面文字判断太宽松, 按钮状态才是实锤。
    返回 (ok, text, n)。
    """
    deadline = time.time() + timeout
    reported = False
    while time.time() < deadline:
        try:
            r = sb.execute_script("""
                (function(){
                    const bs = Array.from(document.querySelectorAll('button'))
                        .filter(b => b.offsetParent !== null && !b.disabled &&
                            /renew for \\d+ days/i.test((b.innerText||b.textContent||'').trim()));
                    if (!bs.length) return {ok:false};
                    return {ok:true, text:(bs[0].innerText||'').trim(), n:bs.length};
                })()
            """)
            if r and r.get("ok"):
                return True, r.get("text"), r.get("n")
        except Exception:
            pass
        if not reported:
            print("⏳ 续期按钮仍处于禁用状态, 等待 Turnstile 校验完成...")
            reported = True
        sb.sleep(2)
    return False, None, None


#   Discord OAuth 登录
DISCORD_CLIENT_ID   = "884382422530158623"
OAUTH_REDIRECT_URI  = "https://bot-hosting.net/login"
OAUTH_SCOPE         = "identify email guilds"
DISCORD_API         = "https://discord.com/api/v9/oauth2/authorize"
DISCORD_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
STATE_RE = re.compile(r"[?&]state=([^&]+)")


def capture_discord_state(sb) -> str:
    print("🔎 获取 Discord OAuth state...")
    sb.uc_open_with_reconnect("https://bot-hosting.net/login/discord", reconnect_time=4)
    time.sleep(2)
    url = sb.get_current_url()
    if "discord.com" not in url:
        print(f"⚠️ 未跳转到 Discord 相关页面，当前 URL：{url}")
        return ""
    m = STATE_RE.search(url)
    if not m:
        print(f"❌ 未能从 URL 中解析出 state，当前 URL：{url}")
        return ""
    state = urllib.parse.unquote(m.group(1))
    print(f"✅ 已捕获 state（当前落地页：{urllib.parse.urlparse(url).path}）")
    return state


def discord_authorize(state: str) -> str:
    query = urllib.parse.urlencode({
        "client_id":     DISCORD_CLIENT_ID,
        "response_type": "code",
        "redirect_uri":  OAUTH_REDIRECT_URI,
        "scope":         OAUTH_SCOPE,
        "state":         state,
    })
    authorize_url = f"{DISCORD_API}?{query}"
    referer = (
        "https://discord.com/oauth2/authorize?" +
        urllib.parse.urlencode({
            "client_id":     DISCORD_CLIENT_ID,
            "redirect_uri":  OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope":         OAUTH_SCOPE,
            "state":         state,
        })
    )
    headers = {
        "accept":           "*/*",
        "authorization":    DC_TOKEN,
        "content-type":     "application/json",
        "origin":           "https://discord.com",
        "referer":          referer,
        "user-agent":       DISCORD_UA,
        "x-discord-locale": "zh-CN",
    }
    body = json.dumps({
        "permissions": "0",
        "authorize": True,
        "integration_type": 0,
        "location_context": {
            "guild_id": "10000",
            "channel_id": "10000",
            "channel_type": 10000,
        },
    })
    proxies = None
    _is_proxy = os.environ.get("IS_PROXY", "false").lower() == "true"
    _proxy_server = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1080"
    if _is_proxy:
        proxies = {"http": _proxy_server, "https": _proxy_server}
    try:
        resp = requests.post(authorize_url, headers=headers, data=body, proxies=proxies, timeout=20)
        if resp.status_code != 200:
            print(f"❌ Discord OAuth2 授权失败: HTTP {resp.status_code} - {resp.text[:300]}")
            return ""
        resp_data = resp.json()
    except Exception as e:
        print(f"❌ Discord OAuth2 授权异常: {e}")
        return ""
    location = resp_data.get("location", "")
    if not location:
        print(f"❌ 授权响应中未找到 location 字段: {resp_data}")
        return ""
    masked = re.sub(r"code=[^&]+", "code=***", location)
    print(f"✅ 拿到回调 URL: {masked}")
    return location


def do_discord_login(sb) -> bool:
    print("\n🔑 通过 Discord Token 登录...")
    state = capture_discord_state(sb)
    if not state:
        sb.save_screenshot("login_no_state.png")
        return False
    location = discord_authorize(state)
    if not location:
        return False
    print("↩️ 携带授权码打开回调链接...")
    sb.uc_open_with_reconnect(location, reconnect_time=4)
    time.sleep(3)
    url = sb.get_current_url()
    if "/error/banned" in url:
        print("🚫 账号已被封禁")
        sb.save_screenshot("login_banned.png")
        return False
    if "bot-hosting.net" not in url:
        print(f"❌ 回调后未跳转至 bot-hosting.net，当前 URL：{url}")
        sb.save_screenshot("login_no_redirect.png")
        return False
    try:
        body_text = sb.get_text("body")
    except Exception:
        body_text = ""
    if "fraud" in body_text.lower():
        print("🚫 触发风控（fraud attempt），可能是 IP 被拦截")
        sb.save_screenshot("login_fraud.png")
        return False
    for _ in range(30):
        url = sb.get_current_url()
        path = urllib.parse.urlparse(url).path
        if "bot-hosting.net" in url and path != "/login" and not path.startswith("/login/discord"):
            print(f"✅ Discord OAuth 登录成功！当前页面：{url}")
            return True
        time.sleep(0.5)
    print(f"❌ 登录超时或未跳转成功，最终停留在：{url}")
    try:
        body_text = sb.get_text("body")[:300]
        print(f"   📝 回调页面内容: {body_text}")
    except Exception:
        pass
    sb.save_screenshot("login_timeout.png")
    return False


# 主流程
def main():
    print("#" * 25)
    print("   Bot-hosting 自动续期")
    print("#" * 25)

    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    PROXY_SERVER = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1080"
    HEADLESS = os.environ.get("HEADLESS", "false").lower() == "true" 

    sb_kwargs = {"uc": True, "headless": HEADLESS}

    if IS_PROXY:
        print(f"🔗 挂载代理: {PROXY_SERVER}")
        sb_kwargs["proxy"] = PROXY_SERVER
    else:
        print("🍭 未使用代理，直连访问")

    global _LOGIN_METHOD

    # 在函数内声明全局变量
    global SESSION_TOKEN
    SESSION_TOKEN = os.environ.get("SESSION_TOKEN") or ""
    global DC_TOKEN
    DC_TOKEN = os.environ.get("DISCORD_TOKEN", "").split(",", 1)[-1].strip() if os.environ.get("DISCORD_TOKEN") else ""

    # 防呆: 若把整段 cookie 字符串 (如 "XSRF-TOKEN=...; __Host-aclclouds_session=...") 贴进
    # SESSION_TOKEN, 自动提取 session_token 纯值并警告
    if SESSION_TOKEN and (";" in SESSION_TOKEN or SESSION_TOKEN.startswith("session_token=")):
        print("⚠️ 检测到 SESSION_TOKEN 含 ';' 或带了 'session_token=' 前缀")
        print("   bot-hosting 的 SESSION_TOKEN 只填 session_token 的纯值 (eyJhbGci 开头),")
        print("   不要贴 XSRF-TOKEN=... 之类的整段 cookie 字符串")
        m = re.search(r"(?:^|;\s*)session_token=([^;]+)", SESSION_TOKEN)
        if m:
            SESSION_TOKEN = m.group(1).strip()
            print(f"   ✅ 已自动提取 session_token 纯值 (长度={len(SESSION_TOKEN)}, 前 8 位={SESSION_TOKEN[:8]}...)")
        else:
            SESSION_TOKEN = ""
            print("   ❌ 未找到 session_token= 字段, 本次按未配置处理")

    # 检查 SESSION_TOKEN 是否有效
    if SESSION_TOKEN:
        print(f"🔍 检查 SESSION_TOKEN: 长度={len(SESSION_TOKEN)}, 前 8 位={SESSION_TOKEN[:8]}...")
        if len(SESSION_TOKEN) < 10:
            print("❌ SESSION_TOKEN 长度不足 10 位，可能为空或无效")
            SESSION_TOKEN = ""
    else:
        print("⚠️ SESSION_TOKEN 未配置")

    with SB(**sb_kwargs) as sb:
        try:
            ip = get_current_ip(PROXY_SERVER if IS_PROXY else "")
            print(f"📍 当前出口 IP: {ip}")
        except Exception as e:
            print(f"⚠️ 获取出口 IP 失败: {e}")

        login_ok = False

        # 方式 1: SESSION_TOKEN Cookie 登录（默认）
        if SESSION_TOKEN:
            print("🚀 启动浏览器...")
            sb.open("https://bot-hosting.net/")
            sb.wait_for_ready_state_complete()
            sb.sleep(2)
            current_url = sb.get_current_url()
            print(f"📝 当前页面 URL: {current_url}")

            # 诊断: 打印当前站点已有 cookie 名 (判断 session cookie 的真实名字)
            try:
                cookies = sb.get_cookies()
                names = [c.get("name") for c in cookies]
                print(f"📝 当前站点 cookie 名: {names}")
            except Exception as e:
                print(f"⚠️ 读取 cookie 名失败: {e}")

            print("📝 注入 Cookie...")
            try:
                sb.add_cookie({"name": "login", "value": "true", "domain": "bot-hosting.net", "path": "/"})
                print("✅ Cookie 'login' 注入成功")
                
                sb.add_cookie({"name": "theme", "value": "system", "domain": "bot-hosting.net", "path": "/"})
                print("✅ Cookie 'theme' 注入成功")
                
                # 使用 JavaScript 注入 session_token，正确处理特殊字符
                escaped_token = SESSION_TOKEN.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')
                js_code = f"""
                (function() {{
                    document.cookie = "session_token={escaped_token}; domain=bot-hosting.net; path=/; SameSite=Lax";
                    return document.cookie;
                }})()
                """
                try:
                    sb.execute_script(js_code)
                except Exception as e:
                    print(f"⚠️ JS 注入 session_token 异常: {e}")
                sb.sleep(1)

                # 校验 cookie 是否真的写入 (JS 注入的返回值有时拿不到, 直接查 cookie)
                def _has_session_cookie():
                    try:
                        ck = sb.get_cookie("session_token")
                        return bool(ck and ck.get("value"))
                    except Exception:
                        return False

                if _has_session_cookie():
                    print("✅ Cookie 'session_token' 注入成功")
                else:
                    print("⚠️ JS 注入 session_token 未生效, 尝试 add_cookie 回退...")
                    try:
                        sb.add_cookie({"name": "session_token", "value": SESSION_TOKEN,
                                       "domain": "bot-hosting.net", "path": "/", "sameSite": "Lax"})
                        if _has_session_cookie():
                            print("✅ Cookie 'session_token' 通过 add_cookie 注入成功")
                        else:
                            print("ℹ️ Cookie 注入校验未通过 (get_cookie 检测不到), 以实际登录结果为准")
                    except Exception as e:
                        print(f"⚠️ add_cookie 回退失败: {e}")
                
                # 刷新页面以应用 cookie
                sb.open("https://bot-hosting.net/a/billings")
                sb.wait_for_ready_state_complete()
                sb.sleep(3)
                
            except Exception as e:
                print(f"❌ Cookie 注入失败: {e}")
                sb.save_screenshot("cookie_injection_failed.png")

            current_url = sb.get_current_url()
            current_title = sb.get_title()
            print(f"📝 当前 URL: {current_url}, Title: {current_title}")

            if "/a/billings" in current_url and "/login" not in current_url and "error=" not in current_url:
                login_ok = True
                print("✅ SESSION_TOKEN 登录成功，当前已到达账单页")
            else:
                print(f"❌ SESSION_TOKEN 登录失败，当前 URL: {current_url}, 当前标题: {current_title}")

        # 方式 2: Discord OAuth 登录（备用）
        if not login_ok and DC_TOKEN:
            _LOGIN_METHOD = "Discord Token"
            print("\n🔄 SESSION_TOKEN 登录失败或未配置，尝试 Discord OAuth 登录...")
            if do_discord_login(sb):
                print("🌐 访问 https://bot-hosting.net/a/billings ...")
                sb.open("https://bot-hosting.net/a/billings")
                sb.wait_for_ready_state_complete()
                sb.sleep(3)
                current_url = sb.get_current_url()
                current_title = sb.get_title()
                print(f"📝 当前 URL: {current_url}, Title: {current_title}")
                if "a/billings" in current_url:
                    login_ok = True
                    print("✅ Discord OAuth 登录成功，当前已到达账单页")
                else:
                    print(f"❌ Discord OAuth 登录后仍未到达账单页，当前 URL: {current_url}")
            else:
                print("❌ Discord OAuth 登录失败")

        if not login_ok:
            error_msg = "Cookie 已失效或页面异常"
            if not SESSION_TOKEN and DC_TOKEN:
                error_msg = "Discord OAuth 登录失败"
            elif SESSION_TOKEN and DC_TOKEN:
                error_msg = "SESSION_TOKEN 和 Discord OAuth 均失败"
            send_telegram_message(format_notification("❌ 登录失败", error=error_msg))
            return

        if _LOGIN_METHOD == "Discord Token":
            print("ℹ️ 本次使用 Discord OAuth 登录，新的 SESSION_TOKEN 将自动更新到 Secrets")

        # 提取当前到期日期
        sb.sleep(2)
        page_source = sb.get_page_source()
        current_expiry = extract_expiry_date(page_source)
        if current_expiry:
            print(f"📅 当前到期日期: {current_expiry}")
        else:
            print("⚠️ 未能提取当前到期日期")

        # 寻找外部续期按钮
        outer_renew_selector = None
        countdown_text = None
        possible_selectors = [
            'button:contains("Renew")',
            'button:contains("Renew free plan")',
            'a:contains("Renew")',
            '[class*="renew"]',
            '[class*="Renew"]',
        ]

        for selector in possible_selectors:
            try:
                if sb.is_element_visible(selector):
                    button_text = sb.get_text(selector)
                    if "Renew in" in button_text:
                        match = re.search(r"Renew in (\d{2}:\d{2}:\d{2})", button_text)
                        if match:
                            countdown_text = match.group(1)
                        break
                    elif "Renew" in button_text and "in" not in button_text.lower():
                        outer_renew_selector = selector
                        print(f"✅ 续期按钮可用: '{button_text}'")
                        break
            except Exception as e:
                pass

        # 点击外部续期按钮等待弹窗
        if outer_renew_selector:
            print("🔄 点击外部续期按钮，等待验证窗口...")
            try:
                sb.sleep(2)
                sb.click(outer_renew_selector)
                sb.sleep(15)
            except Exception as e:
                print(f"❌ 点击外部按钮失败: {e}")
                send_telegram_message(format_notification("❌ 续期失败", error="点击外部续期按钮出错"))
                return

            # 处理弹窗中的 Turnstile
            print("🔒 检测弹窗中的 Turnstile 验证...")
            turnstile_passed = False
            for attempt in range(1, 5):
                try:
                    clicked = sb.uc_gui_click_captcha()
                    print(f"   第 {attempt} 次点击验证框: {'已点击' if clicked else '未找到/无需点击'}")
                except Exception as e:
                    print(f"⚠️ 点击 Turnstile 出错: {e}")
                # 真正通过的信号: 弹窗里续期按钮从禁用变为可用 (Turnstile 响应 token 已生成)
                # 旧版只靠页面文字判断, 校验其实没完成, 按钮一直禁用
                ok, _, _ = wait_for_renew_button(sb, timeout=30)
                if ok:
                    turnstile_passed = True
                    print("✅ Turnstile 验证已通过 (续期按钮已启用)")
                    break
                print(f"⏳ 第 {attempt} 次尝试后续期按钮仍未启用, 重新点击验证框...")
                sb.sleep(3)

            if not turnstile_passed:
                print("❌ Turnstile 验证最终未通过 (续期按钮始终禁用)")
                print("   最常见原因: 出口 IP 被 Cloudflare 风控, 机房/数据中心 IP 很难过 Turnstile")
                print("   → 建议换更干净的住宅/原生代理后重试 (当前出口 IP 见上方日志)")
                save_screenshot(sb, "turnstile_failed")
                send_telegram_message(
                    format_notification("❌ 续期失败", error="Turnstile 验证未通过 (IP 可能被 CF 风控, 建议换干净代理)")
                )
                return

            # 点击续期按钮
            print("⏳ 等待续期按钮可用并点击...")
            time.sleep(3)
            print("🔍 弹窗内按钮结构 (诊断):")
            inspect_renew_buttons(sb)
            save_screenshot(sb, "before_click_renew")

            # 关键: Turnstile 校验未真正完成时按钮是禁用状态, 必须等它启用再点
            btn_ok, btn_text, btn_n = wait_for_renew_button(sb, timeout=45)
            if not btn_ok:
                print("⚠️ 等待 45s 续期按钮仍未启用")
                print("   可能原因: Turnstile 未真正通过 / 免费续期冷却中 / 信用不足")
                inspect_renew_buttons(sb)
                save_screenshot(sb, "button_still_disabled")
            else:
                print(f"✅ 续期按钮已启用: '{btn_text}' (匹配 {btn_n} 个)")

            modal_button_clicked = False
            if btn_ok:
                try:
                    # 用 JS 精确点击"可见且未禁用"的 "Renew for X days" 按钮
                    js = sb.execute_script("""
                        (function(){
                            const bs = Array.from(document.querySelectorAll('button'))
                                .filter(b => b.offsetParent !== null && !b.disabled &&
                                    /renew for \\d+ days/i.test((b.innerText||b.textContent||'').trim()));
                            if (!bs.length) return {ok:false, n:0};
                            bs[0].click();
                            return {ok:true, n:bs.length, text:(bs[0].innerText||'').trim()};
                        })()
                    """)
                    if js.get("ok"):
                        modal_button_clicked = True
                        print(f"✅ 已点击续期按钮: '{js.get('text')}' (可见可用匹配 {js.get('n')} 个)")
                    else:
                        print(f"ℹ️ JS 未找到可见可用的续期按钮, 尝试 CSS 回退")
                except Exception as e:
                    print(f"⚠️ JS 点击异常: {e}")
            if not modal_button_clicked:
                # 回退: 原 CSS 选择器
                try:
                    sb.click('button:contains("Renew for 4 days")', timeout=8)
                    modal_button_clicked = True
                    print("✅ 已点击续期按钮 (CSS 选择器回退)")
                except Exception as e:
                    print(f"续期按钮点击失败: {e}")

            time.sleep(2)
            print("🔍 点击后按钮结构 (诊断):")
            inspect_renew_buttons(sb)
            save_screenshot(sb, "after_click_renew")

            # 部分流程点完 "Renew for 4 days" 后还会弹二次确认框, 补点一次
            for confirm_sel in (
                'button:contains("Confirm")',
                'button:contains("Yes")',
                'button:contains("确认")',
            ):
                try:
                    if sb.is_element_visible(confirm_sel):
                        sb.click(confirm_sel, timeout=5)
                        print(f"✅ 已点击确认按钮: {confirm_sel}")
                        time.sleep(2)
                        break
                except Exception:
                    pass

            print("⏳ 等待新的过期时间...")
            sb.sleep(8)

            # 多信号验证: 倒计时 / 到期日期变化 / 成功文案
            # 未检测到变化时重新打开账单页再查 (续期可能已生效但当前 DOM 未刷新)
            renew_success = False
            new_expiry = None
            new_countdown = None
            for attempt in range(1, 4):
                page_text = sb.get_page_source()
                new_expiry = extract_expiry_date(page_text)
                cd = re.search(r"Renew in (\d{2}:\d{2}:\d{2})", page_text)
                ok_text = re.search(
                    r"(renewed successfully|successfully renewed|renew success|已续期|续期成功)",
                    page_text, re.I)
                if cd:
                    renew_success = True
                    new_countdown = cd.group(1)
                    break
                if new_expiry and new_expiry != current_expiry:
                    renew_success = True
                    break
                if ok_text:
                    renew_success = True
                    break
                if attempt < 3:
                    print(f"⏳ 第 {attempt} 次未检测到变化, 刷新账单页重试...")
                    try:
                        sb.open("https://bot-hosting.net/a/billings")
                        sb.wait_for_ready_state_complete()
                        sb.sleep(6)
                    except Exception as e:
                        print(f"⚠️ 刷新账单页失败: {e}")
                        break

            if renew_success:
                if new_countdown:
                    print(f"✅ 续期成功！新的倒计时: {new_countdown}")
                    if new_expiry:
                        print(f"📅 新的到期日期: {new_expiry}")
                    send_telegram_message(
                        format_notification(
                            "✅ 续期成功",
                            extra=f"⏱️ 可续期时间: {format_countdown(new_countdown)} 后",
                            expiry_date=new_expiry or "（未获取到）"
                        )
                    )
                else:
                    print(f"✅ 续期成功，到期日期已更新为: {new_expiry}")
                    send_telegram_message(
                        format_notification(
                            "✅ 续期成功",
                            extra="到期日期已更新",
                            expiry_date=new_expiry
                        )
                    )
            else:
                print("⚠️ 续期结果未知，到期日期未变化，请手动检查")
                send_telegram_message(
                    format_notification(
                        "⚠️ 续期可能未成功",
                        extra="请登录后台检查",
                        expiry_date=current_expiry or "（未获取到）"
                    )
                )

        else:
            if countdown_text:
                friendly = format_countdown(countdown_text)
                print(f"⏳ 未到续期时间，倒计时: {countdown_text} ({friendly})")
                send_telegram_message(
                    format_notification(
                        "⏳ 未到续期时间",
                        extra=f"⏱️ 可续期时间: {friendly} 后",
                        expiry_date=current_expiry or "（未获取到）"
                    )
                )
            else:
                print("ℹ️ 未找到续期按钮或倒计时，状态未知")
                send_telegram_message(
                    format_notification(
                        "ℹ️ 无需续期",
                        extra="当前状态未知，请手动检查",
                        expiry_date=current_expiry or "（未获取到）"
                    )
                )

        # 更新 SESSION_TOKEN 
        print("🔄 检查 SESSION_TOKEN 是否需要更新")
        new_token, token_expiry = get_cookie_info(sb, "session_token")
        old_token = SESSION_TOKEN

        if should_update_cookie(new_token, old_token, token_expiry):
            print("🔄 SESSION_TOKEN 需要更新")
            if GH_TOKEN:
                if update_github_secret("SESSION_TOKEN", new_token):
                    print("✅ SESSION_TOKEN 更新成功")
                else:
                    print("⚠️ 更新失败，请检查 GH_TOKEN 权限")
            else:
                print("⚠️ 未设置 GH_TOKEN，无法自动更新")
                print(f"📋 请手动设置 SESSION_TOKEN = {new_token[:4]}...{new_token[-4:]}")
        else:
            print("✅ SESSION_TOKEN 无需更新")
        
        print("🏁 脚本执行完毕")

if __name__ == "__main__":
    main()