"""
Bybit 펀딩비 텔레그램 봇 - Vercel Serverless Function
"""
import os
import json
import hmac
import hashlib
import time
from http.server import BaseHTTPRequestHandler
import urllib.request
import urllib.parse


# ============ Bybit API ============

def bybit_request(endpoint: str, params: dict = None) -> dict:
    """Bybit API 요청"""
    base_url = "https://api.bybit.com"
    url = f"{base_url}{endpoint}"

    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url)
    req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode())
        if data.get("retCode") != 0:
            raise Exception(data.get("retMsg"))
        return data.get("result", {})


def get_funding_rates(limit: int = 50) -> list:
    """펀딩비 상위 목록 조회"""
    result = bybit_request("/v5/market/tickers", {"category": "linear"})
    tickers = result.get("list", [])

    funding_list = []
    for ticker in tickers:
        funding_rate = ticker.get("fundingRate")
        if funding_rate:
            rate = float(funding_rate)
            funding_list.append({
                "symbol": ticker.get("symbol", ""),
                "funding_rate": rate,
                "funding_rate_pct": rate * 100,
                "abs_funding_rate": abs(rate)
            })

    funding_list.sort(key=lambda x: x["abs_funding_rate"], reverse=True)
    return funding_list[:limit]


def get_portfolio() -> dict:
    """포트폴리오 조회 (서명 필요)"""
    api_key = os.environ.get("BYBIT_API_KEY", "")
    api_secret = os.environ.get("BYBIT_API_SECRET", "")

    if not api_key or not api_secret:
        return {"error": "API 키가 설정되지 않았습니다"}

    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    params = {"accountType": "UNIFIED"}
    param_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))

    sign_str = f"{timestamp}{api_key}{recv_window}{param_str}"
    signature = hmac.new(
        api_secret.encode('utf-8'),
        sign_str.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    url = f"https://api.bybit.com/v5/account/wallet-balance?{param_str}"
    req = urllib.request.Request(url)
    req.add_header("X-BAPI-API-KEY", api_key)
    req.add_header("X-BAPI-SIGN", signature)
    req.add_header("X-BAPI-TIMESTAMP", timestamp)
    req.add_header("X-BAPI-RECV-WINDOW", recv_window)

    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode())
        return data.get("result", {})


def get_positions() -> list:
    """포지션 조회"""
    api_key = os.environ.get("BYBIT_API_KEY", "")
    api_secret = os.environ.get("BYBIT_API_SECRET", "")

    if not api_key or not api_secret:
        return []

    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    params = {"category": "linear"}
    param_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))

    sign_str = f"{timestamp}{api_key}{recv_window}{param_str}"
    signature = hmac.new(
        api_secret.encode('utf-8'),
        sign_str.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    url = f"https://api.bybit.com/v5/position/list?{param_str}"
    req = urllib.request.Request(url)
    req.add_header("X-BAPI-API-KEY", api_key)
    req.add_header("X-BAPI-SIGN", signature)
    req.add_header("X-BAPI-TIMESTAMP", timestamp)
    req.add_header("X-BAPI-RECV-WINDOW", recv_window)

    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode())
        return data.get("result", {}).get("list", [])


# ============ Telegram ============

def send_telegram(chat_id: int, text: str) -> bool:
    """텔레그램 메시지 전송"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    data = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }).encode('utf-8')

    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 200
    except:
        return False


# ============ Command Handlers ============

def cmd_help(chat_id: int):
    text = """<b>Bybit 펀딩비 봇</b>

<b>명령어:</b>
/funding [N] - 펀딩비 상위 N개 (기본 20)
/f [N] - /funding 단축어

/top [N] - 양수 펀딩비 상위 (롱 과열)
/bottom [N] - 음수 펀딩비 상위 (숏 과열)

/portfolio - 포트폴리오 조회
/p - /portfolio 단축어

/help - 도움말"""
    send_telegram(chat_id, text)


def cmd_funding(chat_id: int, args: str):
    try:
        limit = int(args) if args.strip().isdigit() else 20
        limit = min(limit, 50)

        funding_list = get_funding_rates(limit)
        lines = [f"<b>펀딩비 상위 {limit}개</b>\n"]

        for i, f in enumerate(funding_list[:limit], 1):
            rate = f["funding_rate_pct"]
            sign = "+" if rate > 0 else ""
            emoji = "🔴" if rate < 0 else "🟢"
            lines.append(f"{i}. {emoji} <code>{f['symbol']:<12}</code> {sign}{rate:.4f}%")

        positive = sum(1 for f in funding_list[:limit] if f["funding_rate"] > 0)
        negative = limit - positive
        lines.append(f"\n🟢 롱과열: {positive}개 | 🔴 숏과열: {negative}개")

        send_telegram(chat_id, "\n".join(lines))
    except Exception as e:
        send_telegram(chat_id, f"오류: {str(e)}")


def cmd_top_bottom(chat_id: int, args: str, positive: bool):
    try:
        limit = int(args) if args.strip().isdigit() else 10
        limit = min(limit, 30)

        funding_list = get_funding_rates(200)

        if positive:
            filtered = [f for f in funding_list if f["funding_rate"] > 0]
            title = f"🟢 <b>양수 펀딩비 상위 {limit}개</b> (롱 과열)"
        else:
            filtered = [f for f in funding_list if f["funding_rate"] < 0]
            title = f"🔴 <b>음수 펀딩비 상위 {limit}개</b> (숏 과열)"

        filtered.sort(key=lambda x: abs(x["funding_rate"]), reverse=True)

        lines = [title + "\n"]
        for i, f in enumerate(filtered[:limit], 1):
            rate = f["funding_rate_pct"]
            sign = "+" if rate > 0 else ""
            lines.append(f"{i}. <code>{f['symbol']:<12}</code> {sign}{rate:.4f}%")

        send_telegram(chat_id, "\n".join(lines))
    except Exception as e:
        send_telegram(chat_id, f"오류: {str(e)}")


def cmd_portfolio(chat_id: int):
    try:
        wallet_data = get_portfolio()
        positions = get_positions()

        if "error" in wallet_data:
            send_telegram(chat_id, wallet_data["error"])
            return

        lines = ["<b>📊 포트폴리오</b>\n"]

        if wallet_data.get("list"):
            coins = wallet_data["list"][0].get("coin", [])
            for coin in coins:
                if coin.get("coin") == "USDT":
                    equity = float(coin.get("equity", 0))
                    available = float(coin.get("availableToWithdraw", 0))
                    lines.append("<b>💵 USDT</b>")
                    lines.append(f"총 자산: {equity:.2f}")
                    lines.append(f"가용: {available:.2f}\n")
                    break

        active = [p for p in positions if float(p.get("size", 0)) > 0]

        if active:
            lines.append(f"<b>📈 포지션 ({len(active)}개)</b>")
            for pos in active:
                symbol = pos.get("symbol", "")
                side = pos.get("side", "")
                pnl = float(pos.get("unrealisedPnl", 0))
                leverage = pos.get("leverage", "1")

                direction = "🟢L" if side == "Buy" else "🔴S"
                pnl_sign = "+" if pnl >= 0 else ""

                lines.append(f"<code>{symbol}</code> {direction} x{leverage} | {pnl_sign}{pnl:.2f}")
        else:
            lines.append("포지션 없음")

        send_telegram(chat_id, "\n".join(lines))
    except Exception as e:
        send_telegram(chat_id, f"오류: {str(e)}")


def handle_message(message: dict):
    """메시지 처리"""
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id or not text:
        return

    parts = text.split(maxsplit=1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    # @봇이름 제거
    if "@" in command:
        command = command.split("@")[0]

    if command in ["/start", "/help"]:
        cmd_help(chat_id)
    elif command in ["/funding", "/f"]:
        cmd_funding(chat_id, args)
    elif command == "/top":
        cmd_top_bottom(chat_id, args, positive=True)
    elif command == "/bottom":
        cmd_top_bottom(chat_id, args, positive=False)
    elif command in ["/portfolio", "/p"]:
        cmd_portfolio(chat_id)


# ============ Vercel Handler ============

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        try:
            update = json.loads(body.decode('utf-8'))
            message = update.get("message", {})
            if message:
                handle_message(message)
        except Exception as e:
            print(f"Error: {e}")

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bybit Funding Bot is running!')
