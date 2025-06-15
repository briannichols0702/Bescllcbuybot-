import os
from web3 import Web3
from web3.middleware import geth_poa_middleware
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
import json
from datetime import datetime, timedelta
from pymongo import MongoClient
import logging
from http import HTTPStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
VSC_RPC_URL = "https://rpc.vscblockchain.org"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
MONGO_URI = os.getenv("MONGO_URI")
BUY_GIF_URL = "https://media.giphy.com/media/3o6ZtaO9BZHcOjmErm/giphy.gif"

# Web3 Setup
w3 = Web3(Web3.HTTPProvider(VSC_RPC_URL))
w3.middleware_onion.inject(geth_poa_middleware, layer=0)

# Contract Addresses
BESC_CA = "0x674f3d5ae8f6E0320e24522b77B853a671Bee7b0"
VSG_CA = "0x83048f0Bf34FEeD8CEd419455a4320A735a92e9d"
BESC_VSG_PAIR = "0x80216abe4ace3cd7cd923df826cf81da47e8e958"
BESC_BUSDC_PAIR = "0xd321497f2f85a21fb94eefb21294e418fae421ab"
MONEY_BESC_PAIR = "0xdf9672edc87e198197dc3fa64997a99bab9aba54"
BUSDC_CA = "0x148851477f0c7128DCDaaC64fa011814e785A978"
MONEY_CA = "0xAf8e4A9b508efda0502ed4DCabDbdc2F73AEa1CE"

# Decimals
DECIMALS = {"BESC": 9, "VSG": 18, "BUSDC": 6, "Money": 18}

# ABIs
PAIR_ABI = json.loads('''
[{"constant":true,"inputs":[],"name":"getReserves","outputs":[{"internalType":"uint112","name":"_reserve0","type":"uint112"},{"internalType":"uint112","name":"_reserve1","type":"uint112"},{"internalType":"uint32","name":"_blockTimestampLast","type":"uint32"}],"stateMutability":"view","type":"function"},
 {"constant":true,"inputs":[],"name":"token0","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},
 {"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"sender","type":"address"},{"indexed":false,"internalType":"uint256","name":"amount0In","type":"uint256"},{"indexed":false,"internalType":"uint256","name":"amount1In","type":"uint256"},{"indexed":false,"internalType":"uint256","name":"amount0Out","type":"uint256"},{"indexed":false,"internalType":"uint256","name":"amount1Out","type":"uint256"},{"indexed":true,"internalType":"address","name":"to","type":"address"}],"name":"Swap","type":"event"}]
''')
TOKEN_ABI = json.loads('''
[{"constant":true,"inputs":[],"name":"totalSupply","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]
''')

# Contracts
contracts = {
    "BESC-BUSDC": w3.eth.contract(address=BESC_BUSDC_PAIR, abi=PAIR_ABI),
    "BESC-VSG": w3.eth.contract(address=BESC_VSG_PAIR, abi=PAIR_ABI),
    "Money-BESC": w3.eth.contract(address=MONEY_BESC_PAIR, abi=PAIR_ABI)
}
besc_token = w3.eth.contract(address=BESC_CA, abi=TOKEN_ABI)
money_token = w3.eth.contract(address=MONEY_CA, abi=TOKEN_ABI)

# MongoDB
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["vsc_bot"]
prices = db["prices"]
transactions = db["transactions"]
users = db["users"]

# User Settings
def get_user_settings(user_id):
    user = users.find_one({"user_id": user_id}) or {"alerts": True, "thresholds": {}, "wallets": []}
    return user

def update_user_settings(user_id, settings):
    users.update_one({"user_id": user_id}, {"$set": settings}, upsert=True)

# Get Price
def get_price(pair, contract):
    try:
        reserves = contract.functions.getReserves().call()
        token0 = contract.functions.token0().call().lower()
        if pair == "BESC-BUSDC":
            if token0 == BESC_CA.lower():
                price = reserves[1] / reserves[0] * 10 ** (DECIMALS["BESC"] - DECIMALS["BUSDC"])
                liquidity = reserves[1] / 10 ** DECIMALS["BUSDC"]
            else:
                price = reserves[0] / reserves[1] * 10 ** (DECIMALS["BUSDC"] - DECIMALS["BESC"])
                liquidity = reserves[0] / 10 ** DECIMALS["BUSDC"]
            total_supply = besc_token.functions.totalSupply().call() / 10 ** DECIMALS["BESC"]
        elif pair == "BESC-VSG":
            busdc_data = get_price("BESC-BUSDC", contracts["BESC-BUSDC"])
            busdc_price = busdc_data["price"]
            if token0 == BESC_CA.lower():
                vsg_per_besc = reserves[1] / reserves[0] * 10 ** (DECIMALS["BESC"] - DECIMALS["VSG"])
                liquidity = reserves[1] / 10 ** DECIMALS["VSG"] * busdc_price
            else:
                vsg_per_besc = reserves[0] / reserves[1] * 10 ** (DECIMALS["VSG"] - DECIMALS["BESC"])
                liquidity = reserves[0] / 10 ** DECIMALS["VSG"] * busdc_price
            price = vsg_per_besc * busdc_price
            total_supply = besc_token.functions.totalSupply().call() / 10 ** DECIMALS["BESC"]
        elif pair == "Money-BESC":
            busdc_data = get_price("BESC-BUSDC", contracts["BESC-BUSDC"])
            busdc_price = busdc_data["price"]
            if token0 == BESC_CA.lower():
                money_per_besc = reserves[1] / reserves[0] * 10 ** (DECIMALS["BESC"] - DECIMALS["Money"])
                liquidity = reserves[1] / 10 ** DECIMALS["Money"] * busdc_price
            else:
                money_per_besc = reserves[0] / reserves[1] * 10 ** (DECIMALS["Money"] - DECIMALS["BESC"])
                liquidity = reserves[0] / 10 ** DECIMALS["Money"] * busdc_price
            price = money_per_besc * busdc_price
            total_supply = money_token.functions.totalSupply().call() / 10 ** DECIMALS["Money"]
        market_cap = price * total_supply
        volume_24h = sum(tx["usd_value"] for tx in transactions.find({
            "pair": pair,
            "timestamp": {"$gt": (datetime.now() - timedelta(hours=24)).timestamp()}
        }))
        data = {"price": price, "liquidity": liquidity, "market_cap": market_cap, "volume_24h": volume_24h}
        prices.insert_one({**data, "pair": pair, "timestamp": datetime.now()})
        return data
    except Exception as e:
        logger.error(f"Price error for {pair}: {e}")
        return {"price": 0, "liquidity": 0, "market_cap": 0, "volume_24h": 0}

# Vercel Handler
async def handler(req):
    bot = Bot(TELEGRAM_TOKEN)
    body = await req.json() if req.method == "POST" else {}
    command = body.get("message", {}).get("text", "")
    chat_id = body.get("message", {}).get("chat", {}).get("id", CHAT_ID)
    user_id = body.get("message", {}).get("from", {}).get("id", 0)

    if command == "/start":
        update_user_settings(user_id, {"alerts": True, "thresholds": {}, "wallets": [], "chat_id": chat_id})
        await bot.send_message(
            chat_id=chat_id,
            text="Welcome to BESC Bot! 🚀\n/stats <pair> - View stats\n/setalert price > 0.1\n/portfolio\n/addwallet <address>\n/alerts on/off"
        )
    elif command.startswith("/chart"):
        await bot.send_message(chat_id=chart_id, text="Charting disabled to reduce function size.")
    elif command.startswith("/stats"):
        pair = command.split()[1] if len(command.split()) > 1 else "BESC-BUSDC"
        if pair not in contracts:
            await bot.send_message(chat_id=chat_id, text="Use: BESC-BUSDC, BESC-VSG, Money-BESC")
            return {"statusCode": HTTPStatus.OK}
        metrics = get_price(pair, contracts[pair])
        reply = f"📊 *{pair} Stats*\n" \
                f"Price: ${metrics['price']:.6f}\n" \
                f"Market Cap: ${metrics['market_cap']:,.2f}\n" \
                f"Liquidity: ${metrics['liquidity']:,.2f}\n" \
                f"24h Volume: ${metrics['volume_24h']:,.2f}"
        await bot.send_message(chat_id=chat_id, text=reply, parse_mode="Markdown")
    elif command.startswith("/setalert"):
        args = command.split()[1:]
        if not args:
            await bot.send_message(chat_id=chat_id, text="Use: /setalert price > 0.1")
            return {"statusCode": HTTPStatus.OK}
        try:
            threshold = float(args[2])
            settings = get_user_settings(user_id)
            settings["thresholds"]["price"] = threshold
            update_user_settings(user_id, settings)
            await bot.send_message(chat_id=chat_id, text=f"Alert set for price {args[1]} {threshold}")
        except:
            await bot.send_message(chat_id=chat_id, text="Invalid format.")
    elif command.startswith("/alerts"):
        args = command.split()[1] if len(command.split()) > 1 else ""
        settings = get_user_settings(user_id)
        settings["alerts"] = args.lower() == "on"
        update_user_settings(user_id, settings)
        await bot.send_message(chat_id=chat_id, text=f"Alerts {'enabled' if settings['alerts'] else 'disabled'}.")
    elif command.startswith("/portfolio"):
        settings = get_user_settings(user_id)
        wallets = settings.get("wallets", [])
        if not wallets:
            await bot.send_message(chat_id=chat_id, text="No wallets. Use /addwallet <address>.")
            return {"statusCode": HTTPStatus.OK}
        reply = "💼 *Portfolio*\n"
        for wallet in wallets:
            try:
                balance = w3.eth.get_balance(wallet) / 10 ** 18
                reply += f"Wallet {wallet[:6]}...: {balance:.4f} VSG\n"
            except:
                reply += f"Wallet {wallet[:6]}...: Error fetching balance\n"
        await bot.send_message(chat_id=chat_id, text=reply, parse_mode="Markdown")
    elif command.startswith("/addwallet"):
        wallet = command.split()[1] if len(command.split()) > 1 else ""
        if not wallet or not w3.isAddress(wallet):
            await bot.send_message(chat_id=chat_id, text="Invalid wallet address.")
            return {"statusCode": HTTPStatus.OK}
        settings = get_user_settings(user_id)
        settings["wallets"] = settings.get("wallets", []) + [wallet]
        update_user_settings(user_id, settings)
        await bot.send_message(chat_id=chat_id, text=f"Wallet {wallet[:6]}... added.")
    elif body.get("callback_query"):
        await bot.send_message(chat_id=chat_id, text="Charting disabled.")
    return {"statusCode": HTTPStatus.OK}

# Daily Cron for Swap Monitoring
async def monitor_swaps():
    bot = Bot(TELEGRAM_TOKEN)
    latest_block = w3.eth.get_block('latest').number
    start_block = latest_block - 6050  # ~24h
    for pair, contract in contracts.items():
        try:
            events = contract.events.Swap.getLogs(fromBlock=start_block, toBlock=latest_block)
            for event in events:
                tx_hash = event['transactionHash'].hex()
                if transactions.find_one({"tx_hash": tx_hash}):
                    continue
                amount0_in = event['args']['amount0In']
                amount1_in = event['args']['amount1In']
                amount0_out = event['args']['amount0Out']
                amount1_out = event['args']['amount1Out']
                to = event['args']['to']
                token0 = contract.functions.token0().call().lower()
                is_buy = False
                amount = 0
                token_name = "BESC" if pair != "Money-BESC" else "Money"
                if pair == "BESC-BUSDC" and token0 == BESC_CA.lower() and amount1_in > 0 and amount0_out > 0:
                    is_buy = True
                    amount = amount0_out / 10 ** DECIMALS["BESC"]
                elif pair == "BESC-BUSDC" and token0 == BUSDC_CA.lower() and amount0_in > 0 and amount1_out > 0:
                    is_buy = True
                    amount = amount1_out / 10 ** DECIMALS["BESC"]
                elif pair == "BESC-VSG" and token0 == BESC_CA.lower() and amount1_in > 0 and amount0_out > 0:
                    is_buy = True
                    amount = amount0_out / 10 ** DECIMALS["BESC"]
                elif pair == "BESC-VSG" and token0 == VSG_CA.lower() and amount0_in > 0 and amount1_out > 0:
                    is_buy = True
                    amount = amount1_out / 10 ** DECIMALS["BESC"]
                elif pair == "Money-BESC" and token0 == BESC_CA.lower() and amount1_in > 0 and amount0_out > 0:
                    is_buy = True
                    amount = amount0_out / 10 ** DECIMALS["Money"]
                elif pair == "Money-BESC" and token0 == MONEY_CA.lower() and amount0_in > 0 and amount1_out > 0:
                    is_buy = True
                    amount = amount1_out / 10 ** DECIMALS["Money"]
                if is_buy:
                    metrics = get_price(pair, contracts[pair])
                    usd_value = amount * metrics['price']
                    for user in users.find({"alerts": True}):
                        if user.get("thresholds", {}).get("price", 0) <= metrics["price"]:
                            alert = f"🔔 *{pair} Buy Alert* 📈\n" \
                                    f"Buyer: {to[:6]}...{to[-4:]}\n" \
                                    f"Amount: {amount:,.2f} {token_name}\n" \
                                    f"USD Value: ${usd_value:,.2f}\n" \
                                    f"Price: ${metrics['price']:.6f}\n" \
                                    f"Market Cap: ${metrics['market_cap']:,.2f}\n" \
                                    f"Liquidity: ${metrics['liquidity']:,.2f}\n" \
                                    f"24h Volume: ${metrics['volume_24h']:,.2f}\n" \
                                    f"Tx: https://explorer.vscblockchain.org/tx/{tx_hash}"
                            transactions.insert_one({
                                "tx_hash": tx_hash,
                                "pair": pair,
                                "amount": amount,
                                "usd_value": usd_value,
                                "price": metrics['price'],
                                "timestamp": datetime.now().timestamp()
                            })
                            await bot.send_animation(
                                chat_id=user.get("chat_id", CHAT_ID),
                                animation=BUY_GIF_URL,
                                caption=alert,
                                parse_mode="Markdown"
                            )
        except Exception as e:
            logger.error(f"Swap error for {pair}: {e}")
    return {"statusCode": HTTPStatus.OK}

def vercel(event, context):
    import asyncio
    if event["path"] == "/api/monitor":
        return asyncio.run(monitor_swaps())
    return asyncio.run(handler(event["body"]))