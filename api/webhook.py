from flask import Flask, request, jsonify
import os
import json
import hmac
import hashlib
import time
import urllib.request
import urllib.parse

app = Flask(__name__)


# ============ Bybit API ============

def bybit_request(endpoint, params=None):
    base_url = "https://api.bybit.com"
    url = f"{base_url}{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")

    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode())
        if data.get("retCode") != 0:
            raise Exception(data.get("retMsg"))
        return data.get("result", {})


def get_funding_rates(limit=50):
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


def bybit_signed_request(endpoint, params):
    api_key = os.environ.get("BYBIT_API_KEY", "")
    api_secret = os.environ.get("BYBIT_API_SECRET", "")

    if not api_key or not api_secret:
        raise Exception("API key not set")

    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    param_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))

    sign_str = f"{timestamp}{api_key}{recv_window}{param_str}"
    signature = hmac.new(
        api_secret.encode(), sign_str.encode(), hashlib.sha256
    ).hexdigest()

    url = f"https://api.bybit.com{endpoint}?{param_str}"
    req = urllib.request.Request(url)
    req.add_header("X-BAPI-API-KEY", api_key)
    req.add_header("X-BAPI-SIGN", signature)
    req.add_header("X-BAPI-TIMESTAMP", timestamp)
    req.add_header("X-BAPI-RECV-WINDOW", recv_window)
    req.add_header("User-Agent", "Mozilla/5.0")

    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode())
        if data.get("retCode") != 0:
            raise Exception(data.get("retMsg"))
        return data.get("result", {})


# ============ Telegram ============

def send_telegram(chat_id, text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    data = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }).encode()

    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 200
    except:
        return False


# ============ Commands ============

def cmd_help(chat_id):
    text = """<b>Bybit 펀딩비 봇</b>

/funding [N] - 펀딩비 상위 N개
/f [N] - /funding 단축어
/top [N] - 양수 펀딩비 (롱 과열)
/bottom [N] - 음수 펀딩비 (숏 과열)
/portfolio - 포트폴리오
/help - 도움말"""
    send_telegram(chat_id, text)


def cmd_funding(chat_id, args):
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


def cmd_top_bottom(chat_id, args, positive):
    try:
        limit = int(args) if args.strip().isdigit() else 10
        limit = min(limit, 30)

        funding_list = get_funding_rates(200)

        if positive:
            filtered = [f for f in funding_list if f["funding_rate"] > 0]
            title = f"🟢 <b>양수 펀딩비 상위 {limit}개</b>"
        else:
            filtered = [f for f in funding_list if f["funding_rate"] < 0]
            title = f"🔴 <b>음수 펀딩비 상위 {limit}개</b>"

        filtered.sort(key=lambda x: abs(x["funding_rate"]), reverse=True)

        lines = [title + "\n"]
        for i, f in enumerate(filtered[:limit], 1):
            rate = f["funding_rate_pct"]
            sign = "+" if rate > 0 else ""
            lines.append(f"{i}. <code>{f['symbol']:<12}</code> {sign}{rate:.4f}%")

        send_telegram(chat_id, "\n".join(lines))
    except Exception as e:
        send_telegram(chat_id, f"오류: {str(e)}")


def cmd_portfolio(chat_id):
    try:
        wallet = bybit_signed_request("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        positions = bybit_signed_request("/v5/position/list", {"category": "linear"}).get("list", [])

        lines = ["<b>📊 포트폴리오</b>\n"]

        if wallet.get("list"):
            for coin in wallet["list"][0].get("coin", []):
                if coin.get("coin") == "USDT":
                    equity = float(coin.get("equity", 0))
                    available = float(coin.get("availableToWithdraw", 0))
                    lines.append(f"💵 총자산: {equity:.2f} USDT")
                    lines.append(f"💵 가용: {available:.2f} USDT\n")
                    break

        active = [p for p in positions if float(p.get("size", 0)) > 0]
        if active:
            lines.append(f"<b>포지션 ({len(active)}개)</b>")
            for pos in active:
                symbol = pos.get("symbol", "")
                side = "🟢L" if pos.get("side") == "Buy" else "🔴S"
                pnl = float(pos.get("unrealisedPnl", 0))
                leverage = pos.get("leverage", "1")
                pnl_sign = "+" if pnl >= 0 else ""
                lines.append(f"<code>{symbol}</code> {side} x{leverage} | {pnl_sign}{pnl:.2f}")
        else:
            lines.append("포지션 없음")

        send_telegram(chat_id, "\n".join(lines))
    except Exception as e:
        send_telegram(chat_id, f"오류: {str(e)}")


def handle_message(message):
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id or not text:
        return

    parts = text.split(maxsplit=1)
    command = parts[0].lower().split("@")[0]
    args = parts[1] if len(parts) > 1 else ""

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


# ============ Routes ============

@app.route("/", methods=["GET"])
def index():
    return "Bybit Funding Bot is running!"


@app.route("/api/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        return "Webhook OK"

    try:
        update = request.get_json()
        message = update.get("message", {})
        if message:
            handle_message(message)
    except Exception as e:
        print(f"Error: {e}")

    return jsonify({"ok": True})


# For Vercel
app = app
