from __future__ import annotations

import asyncio
import json
import os
import random
import threading
from math import comb
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


try:
    import certifi
except ImportError:
    certifi = None
else:
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import discord
from discord.ext import commands


def load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return

    with open(path, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env_file(os.path.join(os.path.dirname(__file__), ".env"))


def start_render_health_server() -> None:
    port = os.getenv("PORT")
    if not port:
        return

    class HealthHandler(BaseHTTPRequestHandler):
        def health_payload(self) -> tuple[bytes, str]:
            if self.path.split("?", 1)[0] == "/health":
                return b'{"status":"ok"}\n', "application/json"
            return b"Casino bot is running.\n", "text/plain"

        def send_health_response(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()

        def do_GET(self) -> None:
            body, content_type = self.health_payload()
            self.send_health_response(body, content_type)
            self.wfile.write(body)

        def do_HEAD(self) -> None:
            body, content_type = self.health_payload()
            self.send_health_response(body, content_type)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", int(port)), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Health server listening on port {port}", flush=True)


STARTING_BALANCE = 250
COMMAND_PREFIX = "."
DEVELOPER_ROLE_NAME = "developer"
COINFLIP_WIN_RATE = 0.50
HIGH_BET_THRESHOLD = 1000
LOW_BET_RETURN = 0.925
HIGH_BET_RETURN = 0.85
MINES_GRID_SIZE = 25
PLINKO_ROWS = 8
PLINKO_BASE_MULTIPLIERS = [8.0, 3.0, 1.5, 0.7, 0.3, 0.7, 1.5, 3.0, 8.0]
PLINKO_ANIMATION_DELAY = 0.25
SLOT_SYMBOLS = ["7", "BAR", "Bell", "Cherry", "Lemon", "Diamond"]
SLOT_WEIGHTS = [1, 2, 4, 6, 8, 10]
SLOT_EMOJIS = {
    "7": "7️⃣",
    "BAR": "🧱",
    "Bell": "🔔",
    "Cherry": "🍒",
    "Lemon": "🍋",
    "Diamond": "💎",
}
SLOT_THREE_MATCH_MULTIPLIERS = {
    "7": 10,
    "BAR": 6,
    "Diamond": 4.5,
    "Bell": 3.5,
    "Cherry": 2.75,
    "Lemon": 2.25,
}
SLOT_TWO_MATCH_MULTIPLIER = 0.75
SLOT_RETURN_MULTIPLIER = 0.85
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_KEY = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
SUPABASE_BALANCES_TABLE = os.getenv("SUPABASE_BALANCES_TABLE", "player_balances")
SUPABASE_COMMAND_MESSAGES_TABLE = os.getenv("SUPABASE_COMMAND_MESSAGES_TABLE", "processed_command_messages")

CARD_VALUES = [11, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10]
CARD_LABELS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
CARD_ALIASES = {
    "A": 0,
    "ACE": 0,
    "2": 1,
    "3": 2,
    "4": 3,
    "5": 4,
    "6": 5,
    "7": 6,
    "8": 7,
    "9": 8,
    "10": 9,
    "T": 9,
    "J": 10,
    "JACK": 10,
    "Q": 11,
    "QUEEN": 11,
    "K": 12,
    "KING": 12,
}

balances: dict[int, float] = {}
processed_command_messages: set[int] = set()
last_command_claim_error: str | None = None
active_blackjack_games: dict[int, "BlackjackGame"] = {}
active_blackjack_views: dict[int, "BlackjackView"] = {}
active_mines_games: dict[int, "MinesGame"] = {}
active_mines_sessions: dict[int, "MinesSession"] = {}
active_plinko_games: dict[int, "PlinkoGame"] = {}
active_plinko_sessions: dict[int, "PlinkoSession"] = {}
promo_codes: dict[str, "PromoCode"] = {}


def money(amount: float) -> str:
    return f"{amount:.2f}"


def bet_return_rate(bet: int) -> float:
    if bet > HIGH_BET_THRESHOLD:
        return HIGH_BET_RETURN
    return LOW_BET_RETURN


def even_money_win_return(bet: int) -> float:
    return 2 * bet_return_rate(bet)


class BalanceStorageError(RuntimeError):
    pass


def supabase_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def http_error_summary(error: HTTPError) -> str:
    try:
        body = error.read().decode("utf-8").strip()
    except Exception:
        body = ""

    if len(body) > 180:
        body = f"{body[:177]}..."

    if body:
        return f"HTTP {error.code}: {body}"
    return f"HTTP {error.code}"


def log_storage_status() -> None:
    storage = "Supabase" if supabase_enabled() else "memory"
    print(f"Balance storage: {storage}", flush=True)
    print(f"SUPABASE_URL set: {'yes' if SUPABASE_URL else 'no'}", flush=True)
    print(f"SUPABASE_SERVICE_ROLE_KEY set: {'yes' if SUPABASE_SERVICE_ROLE_KEY else 'no'}", flush=True)
    print(f"SUPABASE_ANON_KEY fallback set: {'yes' if SUPABASE_ANON_KEY else 'no'}", flush=True)


def supabase_request(
    method: str,
    path: str,
    payload: object | None = None,
    query: dict[str, str] | None = None,
    prefer: str | None = None,
) -> object | None:
    if not supabase_enabled():
        return None

    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if query:
        url = f"{url}?{urlencode(query)}"

    body = None
    headers = {
        "apikey": SUPABASE_KEY or "",
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Accept-Profile": "public",
        "Content-Profile": "public",
    }

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    if prefer is not None:
        headers["Prefer"] = prefer

    request = Request(url, data=body, headers=headers, method=method)
    with urlopen(request, timeout=10) as response:
        response_body = response.read()
        if not response_body:
            return None
        return json.loads(response_body.decode("utf-8"))


def fetch_balance_from_supabase(user_id: int) -> float | None:
    if not supabase_enabled():
        return None

    try:
        rows = supabase_request(
            "GET",
            SUPABASE_BALANCES_TABLE,
            query={
                "select": "balance",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
        print(f"Could not fetch balance for {user_id} from Supabase: {error}")
        raise BalanceStorageError("Balance database is temporarily unavailable. Try again in a minute.") from error

    if isinstance(rows, list) and rows:
        return round(float(rows[0]["balance"]), 2)
    if not isinstance(rows, list):
        raise BalanceStorageError("Balance database returned an unexpected response. Try again in a minute.")
    return None


def save_balance_to_supabase(user_id: int, amount: float) -> None:
    if not supabase_enabled():
        return

    try:
        supabase_request(
            "POST",
            SUPABASE_BALANCES_TABLE,
            payload={
                "user_id": str(user_id),
                "balance": round(amount, 2),
            },
            query={"on_conflict": "user_id"},
            prefer="resolution=merge-duplicates,return=minimal",
        )
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
        print(f"Could not save balance for {user_id} to Supabase: {error}")
        raise BalanceStorageError("Balance database is temporarily unavailable. Try again in a minute.") from error


def leaderboard_balances(limit: int = 10) -> list[tuple[int, float]]:
    if supabase_enabled():
        try:
            rows = supabase_request(
                "GET",
                SUPABASE_BALANCES_TABLE,
                query={
                    "select": "user_id,balance",
                    "order": "balance.desc",
                    "limit": str(limit),
                },
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            print(f"Could not fetch leaderboard from Supabase: {error}")
        else:
            if isinstance(rows, list):
                leaderboard = []
                for row in rows:
                    try:
                        leaderboard.append((int(row["user_id"]), round(float(row["balance"]), 2)))
                    except (KeyError, TypeError, ValueError):
                        continue
                return leaderboard

    return sorted(balances.items(), key=lambda item: item[1], reverse=True)[:limit]


def claim_command_message(message_id: int) -> bool:
    global last_command_claim_error
    last_command_claim_error = None

    if message_id in processed_command_messages:
        return False
    processed_command_messages.add(message_id)

    if not supabase_enabled():
        return True

    try:
        supabase_request(
            "POST",
            SUPABASE_COMMAND_MESSAGES_TABLE,
            payload={"message_id": str(message_id)},
            prefer="return=minimal",
        )
    except HTTPError as error:
        if error.code == 409:
            return False
        last_command_claim_error = f"HTTP {error.code}"
        print(
            f"Could not claim command message {message_id} in Supabase. "
            f"Using local duplicate guard only: {error}",
            flush=True,
        )
        return True
    except (URLError, TimeoutError, OSError, ValueError) as error:
        last_command_claim_error = type(error).__name__
        print(
            f"Could not claim command message {message_id} in Supabase. "
            f"Using local duplicate guard only: {error}",
            flush=True,
        )
        return True

    return True


def balance_for(user_id: int) -> float:
    if user_id not in balances:
        if supabase_enabled():
            balance = fetch_balance_from_supabase(user_id)
            if balance is None:
                balance = float(STARTING_BALANCE)
                save_balance_to_supabase(user_id, balance)
        else:
            balance = float(STARTING_BALANCE)
        balances[user_id] = balance
    return balances[user_id]


def set_balance(user_id: int, amount: float) -> None:
    balance = round(amount, 2)
    save_balance_to_supabase(user_id, balance)
    balances[user_id] = balance


def normalize_promo_code(code: str) -> str:
    return code.strip().upper()


def valid_promo_code(code: str) -> bool:
    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    return 3 <= len(code) <= 32 and all(character in allowed for character in code)


def mines_multiplier(mine_count: int, revealed_safe: int, bet: int) -> float:
    if revealed_safe < 1:
        return 1.0

    safe_squares = MINES_GRID_SIZE - mine_count
    fair_multiplier = comb(MINES_GRID_SIZE, revealed_safe) / comb(safe_squares, revealed_safe)
    return round(fair_multiplier * bet_return_rate(bet), 4)


def plinko_path() -> list[int]:
    positions = [0]
    position = 0
    for _ in range(PLINKO_ROWS):
        position += random.randint(0, 1)
        positions.append(position)
    return positions


def plinko_multiplier(bucket: int, bet: int) -> float:
    return round(PLINKO_BASE_MULTIPLIERS[bucket] * bet_return_rate(bet), 4)


def compact_multiplier(multiplier: float) -> str:
    text = f"{multiplier:.1f}" if multiplier >= 1 else f"{multiplier:.2f}"
    return text.rstrip("0").rstrip(".")


def plinko_board(path: list[int], bet: int, current_row: int | None = None) -> str:
    lines = []
    visible_row = min(current_row, PLINKO_ROWS) if current_row is not None else None

    for row in range(PLINKO_ROWS + 1):
        indent = " " * (PLINKO_ROWS - row)
        cells = []
        for column in range(row + 1):
            has_ball = visible_row is not None and row == visible_row and column == path[visible_row]
            cells.append("🔴" if has_ball else "⚪")
        lines.append(f"{indent}{' '.join(cells)}")

    bucket_labels = " ".join(f"{compact_multiplier(plinko_multiplier(index, bet)):>3}" for index in range(PLINKO_ROWS + 1))
    lines.append(bucket_labels)
    return "\n".join(lines)


def draw_card() -> int:
    return random.randint(0, 12)


def hand_value(cards: list[int]) -> int:
    total = sum(CARD_VALUES[card] for card in cards)
    aces = cards.count(0)
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def hand_display(cards: list[int]) -> str:
    labels = " ".join(CARD_LABELS[card] for card in cards)
    return f"{labels} [{hand_value(cards)}]"


def parse_card(label: str) -> int | None:
    return CARD_ALIASES.get(label.strip().upper())


def parse_cards(labels: tuple[str, ...]) -> list[int] | None:
    cards = []
    for label in labels:
        card = parse_card(label)
        if card is None:
            return None
        cards.append(card)
    return cards


def spin_slots() -> list[str]:
    return random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=3)


def slots_multiplier(reels: list[str]) -> float:
    counts = {symbol: reels.count(symbol) for symbol in set(reels)}
    if 3 in counts.values():
        return SLOT_THREE_MATCH_MULTIPLIERS[reels[0]]
    if 2 in counts.values():
        return SLOT_TWO_MATCH_MULTIPLIER
    return 0.0


def slots_display(reels: list[str]) -> str:
    return " | ".join(SLOT_EMOJIS[symbol] for symbol in reels)


def make_embed(title: str, description: str, color: discord.Color | None = None) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=description,
        color=color or discord.Color.blurple(),
    )


def normalized_bet(balance: float, requested_bet: int) -> tuple[int | None, str | None]:
    if requested_bet < 1:
        return None, "Bet must be at least 1."

    bet = requested_bet
    warning = None
    if bet > balance:
        bet = int(balance)
        warning = "Insufficient balance, bet set to maximum value."

    if bet < 1:
        return None, "You need at least $1.00 to bet."

    return bet, warning


def has_active_blackjack(user_id: int) -> bool:
    return user_id in active_blackjack_games


def active_game_name(user_id: int) -> str | None:
    if user_id in active_blackjack_games:
        return "blackjack"
    if user_id in active_mines_games:
        return "mines"
    if user_id in active_plinko_games:
        return "plinko"
    return None


def has_developer_role(member: discord.abc.User) -> bool:
    return isinstance(member, discord.Member) and any(
        role.name.casefold().strip() == DEVELOPER_ROLE_NAME for role in member.roles
    )


def developer_only() -> commands.Check:
    async def predicate(ctx: commands.Context) -> bool:
        return has_developer_role(ctx.author)

    return commands.check(predicate)


async def require_developer_role(ctx: commands.Context) -> bool:
    if has_developer_role(ctx.author):
        return True

    await ctx.send("You need the developer role to use that command.")
    return False


@dataclass
class PromoCode:
    code: str
    amount: int
    max_people: int
    redeemed_by: set[int] = field(default_factory=set)

    @property
    def people_left(self) -> int:
        return max(0, self.max_people - len(self.redeemed_by))

    def can_redeem(self, user_id: int) -> tuple[bool, str | None]:
        if user_id in self.redeemed_by:
            return False, "You already redeemed that promo code."
        if self.people_left < 1:
            return False, "That promo code has reached its redemption limit."
        return True, None


@dataclass
class BlackjackGame:
    user_id: int
    bet: int
    balance: float
    player_cards: list[int] = field(default_factory=list)
    dealer_cards: list[int] = field(default_factory=list)
    first_turn: bool = True
    finished: bool = False
    result: str = ""
    split_hands: list[list[int]] | None = None
    split_bets: list[int] = field(default_factory=list)
    active_hand: int = 0
    busted_hands: set[int] = field(default_factory=set)
    stood_hands: set[int] = field(default_factory=set)

    @classmethod
    def start(cls, user_id: int, balance: float, bet: int) -> "BlackjackGame":
        game = cls(user_id=user_id, bet=bet, balance=round(balance - bet, 2))
        game.player_cards = [draw_card(), draw_card()]
        game.dealer_cards = [draw_card()]
        return game

    def can_double(self) -> bool:
        return (
            self.first_turn
            and self.split_hands is None
            and len(self.player_cards) == 2
            and self.balance >= self.bet
        )

    def can_split(self) -> bool:
        return (
            self.first_turn
            and self.split_hands is None
            and len(self.player_cards) == 2
            and self.player_cards[0] == self.player_cards[1]
            and self.balance >= self.bet
        )

    def hit(self) -> str:
        if self.finished:
            return "This blackjack game is already over."

        if self.split_hands is not None:
            hand = self.split_hands[self.active_hand]
            hand.append(draw_card())
            total = hand_value(hand)
            if total > 21:
                self.busted_hands.add(self.active_hand)
                message = f"Hand {self.active_hand + 1} busts."
                return self.advance_split_hand(message)
            return f"Hand {self.active_hand + 1} hit."

        self.first_turn = False
        self.player_cards.append(draw_card())
        if hand_value(self.player_cards) > 21:
            self.finished = True
            self.result = "Bust! You lose."
            return self.result
        return "You hit."

    def stand(self) -> str:
        if self.finished:
            return "This blackjack game is already over."

        if self.split_hands is not None:
            self.stood_hands.add(self.active_hand)
            return self.advance_split_hand(f"Hand {self.active_hand + 1} stands.")

        self.first_turn = False
        return self.finish_single_hand("You stand.")

    def double(self) -> str:
        if self.finished:
            return "This blackjack game is already over."

        if not self.can_double():
            if not self.first_turn:
                return "Cannot double after the first turn."
            return "Insufficient balance to double."

        self.first_turn = False
        self.balance = round(self.balance - self.bet, 2)
        self.bet *= 2
        self.player_cards.append(draw_card())

        if hand_value(self.player_cards) > 21:
            self.finished = True
            self.result = "Bust after doubling. You lose."
            return self.result

        return self.finish_single_hand("You doubled.")

    def split(self) -> str:
        if self.finished:
            return "This blackjack game is already over."
        if not self.first_turn:
            return "Cannot split after the first turn."
        if self.balance < self.bet:
            return "Insufficient balance to split."
        if len(self.player_cards) != 2 or self.player_cards[0] != self.player_cards[1]:
            return "Cards are not the same."

        self.first_turn = False
        self.balance = round(self.balance - self.bet, 2)
        self.split_hands = [
            [self.player_cards[0], draw_card()],
            [self.player_cards[1], draw_card()],
        ]
        self.split_bets = [self.bet, self.bet]
        self.active_hand = 0
        return "Split started. Playing hand 1."

    def test_hand(self, cards: list[int]) -> str:
        if self.finished:
            return "This blackjack game is already over."

        if self.split_hands is not None:
            self.split_hands[self.active_hand] = cards
            self.busted_hands.discard(self.active_hand)
            self.stood_hands.discard(self.active_hand)
            if hand_value(cards) > 21:
                self.busted_hands.add(self.active_hand)
                return self.advance_split_hand(f"Test hand {self.active_hand + 1} busts.")
            return f"Test hand {self.active_hand + 1} set to {hand_display(cards)}."

        self.player_cards = cards
        if hand_value(cards) > 21:
            self.finished = True
            self.result = "Test hand busts. You lose."
            return self.result

        return f"Test hand set to {hand_display(cards)}."

    def dealer_draw(self) -> None:
        while hand_value(self.dealer_cards) < 17:
            self.dealer_cards.append(draw_card())

    def finish_single_hand(self, prefix: str) -> str:
        self.dealer_draw()
        player_total = hand_value(self.player_cards)
        dealer_total = hand_value(self.dealer_cards)

        if dealer_total > 21:
            self.balance = round(self.balance + even_money_win_return(self.bet) * self.bet, 2)
            outcome = "Dealer busts. You win."
        elif dealer_total > player_total:
            outcome = "You lost."
        elif dealer_total < player_total:
            self.balance = round(self.balance + even_money_win_return(self.bet) * self.bet, 2)
            outcome = "You win."
        else:
            self.balance = round(self.balance + self.bet, 2)
            outcome = "A tie. Push."

        self.finished = True
        self.result = f"{prefix} {outcome}"
        return self.result

    def advance_split_hand(self, prefix: str) -> str:
        if self.active_hand == 0:
            self.active_hand = 1
            return f"{prefix} Now playing hand 2."

        return self.finish_split_hands(prefix)

    def finish_split_hands(self, prefix: str) -> str:
        self.dealer_draw()
        dealer_total = hand_value(self.dealer_cards)
        results = [prefix]

        for index, hand in enumerate(self.split_hands or []):
            total = hand_value(hand)
            bet = self.split_bets[index]

            if index in self.busted_hands or total > 21:
                results.append(f"Hand {index + 1} lost.")
            elif dealer_total > 21:
                self.balance = round(self.balance + even_money_win_return(bet) * bet, 2)
                results.append(f"Dealer busts. Hand {index + 1} wins.")
            elif dealer_total > total:
                results.append(f"Hand {index + 1} lost.")
            elif dealer_total < total:
                self.balance = round(self.balance + even_money_win_return(bet) * bet, 2)
                results.append(f"Hand {index + 1} wins.")
            else:
                self.balance = round(self.balance + bet, 2)
                results.append(f"Hand {index + 1} ties. Push.")

        self.finished = True
        self.result = " ".join(results)
        return self.result


def blackjack_embed(game: BlackjackGame, notice: str | None = None) -> discord.Embed:
    description = notice or game.result or "Choose an action."
    color = discord.Color.green() if game.finished and "win" in description.lower() else discord.Color.blurple()
    embed = make_embed("Blackjack", description, color)

    if game.finished:
        dealer = hand_display(game.dealer_cards)
    else:
        dealer = f"{CARD_LABELS[game.dealer_cards[0]]} ?"
    embed.add_field(name="Dealer", value=dealer, inline=False)

    if game.split_hands is None:
        embed.add_field(name="Your Hand", value=hand_display(game.player_cards), inline=False)
        embed.add_field(name="Bet", value=f"${money(game.bet)}", inline=True)
    else:
        for index, hand in enumerate(game.split_hands):
            state = "playing" if index == game.active_hand and not game.finished else "waiting"
            if index in game.busted_hands:
                state = "bust"
            elif index in game.stood_hands:
                state = "stand"
            if game.finished:
                state = "finished"
            embed.add_field(
                name=f"Hand {index + 1} ({state})",
                value=f"{hand_display(hand)} | Bet ${money(game.split_bets[index])}",
                inline=False,
            )

    embed.add_field(name="Balance", value=f"${money(game.balance)}", inline=True)
    return embed


class BlackjackView(discord.ui.View):
    def __init__(self, game: BlackjackGame) -> None:
        super().__init__(timeout=180)
        self.game = game
        self.message: discord.Message | None = None
        self.refresh_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.game.user_id:
            return True

        await interaction.response.send_message("Only the player who started this hand can use these buttons.", ephemeral=True)
        return False

    async def on_timeout(self) -> None:
        if self.game.finished:
            return

        self.game.finished = True
        self.game.result = "Game timed out. Current bet was forfeited."
        active_blackjack_games.pop(self.game.user_id, None)
        active_blackjack_views.pop(self.game.user_id, None)
        try:
            set_balance(self.game.user_id, self.game.balance)
        except BalanceStorageError as error:
            print(f"Could not save timed-out blackjack game for {self.game.user_id}: {error}")
        self.disable_buttons()

        if self.message is not None:
            await self.message.edit(embed=blackjack_embed(self.game), view=self)

    def refresh_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = self.game.finished

        self.set_button_disabled("Double", self.game.finished or not self.game.can_double())
        self.set_button_disabled("Split", self.game.finished or not self.game.can_split())

    def disable_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    def set_button_disabled(self, label: str, disabled: bool) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.label == label:
                item.disabled = disabled

    async def send_storage_error(self, interaction: discord.Interaction, error: BalanceStorageError) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(str(error), ephemeral=True)
        else:
            await interaction.response.send_message(str(error), ephemeral=True)

    async def update_game(self, interaction: discord.Interaction, notice: str) -> None:
        try:
            set_balance(self.game.user_id, self.game.balance)
        except BalanceStorageError as error:
            await self.send_storage_error(interaction, error)
            return

        if self.game.finished:
            active_blackjack_games.pop(self.game.user_id, None)
            active_blackjack_views.pop(self.game.user_id, None)
            self.disable_buttons()
        else:
            self.refresh_buttons()

        await interaction.response.edit_message(embed=blackjack_embed(self.game, notice), view=self)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.update_game(interaction, self.game.hit())

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.update_game(interaction, self.game.stand())

    @discord.ui.button(label="Double", style=discord.ButtonStyle.success)
    async def double_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.update_game(interaction, self.game.double())

    @discord.ui.button(label="Split", style=discord.ButtonStyle.secondary)
    async def split_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.update_game(interaction, self.game.split())


@dataclass
class MinesGame:
    user_id: int
    bet: int
    mine_count: int
    balance: float
    mines: set[int]
    revealed_safe: set[int] = field(default_factory=set)
    finished: bool = False
    result: str = ""

    @classmethod
    def start(cls, user_id: int, balance: float, bet: int, mine_count: int) -> "MinesGame":
        mines = set(random.sample(range(MINES_GRID_SIZE), mine_count))
        return cls(
            user_id=user_id,
            bet=bet,
            mine_count=mine_count,
            balance=round(balance - bet, 2),
            mines=mines,
        )

    @property
    def safe_total(self) -> int:
        return MINES_GRID_SIZE - self.mine_count

    def current_multiplier(self) -> float:
        return mines_multiplier(self.mine_count, len(self.revealed_safe), self.bet)

    def current_payout(self) -> float:
        return round(self.bet * self.current_multiplier(), 2)

    def reveal(self, index: int) -> str:
        if self.finished:
            return "This mines game is already over."
        if index in self.revealed_safe:
            return "That square is already revealed."
        if index in self.mines:
            self.finished = True
            self.result = f"Square {index + 1} had a mine. You lose ${money(self.bet)}."
            return self.result

        self.revealed_safe.add(index)
        if len(self.revealed_safe) >= self.safe_total:
            return self.cash_out(f"Square {index + 1} was safe. All safe squares revealed.")

        payout = self.current_payout()
        multiplier = self.current_multiplier()
        return f"Square {index + 1} was safe. Current cashout: ${money(payout)} ({money(multiplier)}x)."

    def cash_out(self, prefix: str | None = None) -> str:
        if self.finished:
            return "This mines game is already over."
        if not self.revealed_safe:
            return "Reveal at least one safe square before cashing out."

        payout = self.current_payout()
        multiplier = self.current_multiplier()
        profit = round(payout - self.bet, 2)
        self.balance = round(self.balance + payout, 2)
        self.finished = True
        outcome = f"Cashed out for ${money(payout)} ({money(multiplier)}x). Profit: ${money(profit)}."
        self.result = f"{prefix} {outcome}" if prefix else outcome
        return self.result


def mines_embed(game: MinesGame, notice: str | None = None) -> discord.Embed:
    description = notice or game.result or "Pick a square."
    if game.finished and ("lose" in description.lower() or "timed out" in description.lower()):
        color = discord.Color.red()
    elif game.finished:
        color = discord.Color.green()
    else:
        color = discord.Color.blurple()

    embed = make_embed("Mines", description, color)
    embed.add_field(name="Bet", value=f"${money(game.bet)}", inline=True)
    embed.add_field(name="Mines", value=f"{game.mine_count}/{MINES_GRID_SIZE}", inline=True)
    embed.add_field(name="Safe Picks", value=f"{len(game.revealed_safe)}/{game.safe_total}", inline=True)

    if game.revealed_safe:
        embed.add_field(name="Multiplier", value=f"{money(game.current_multiplier())}x", inline=True)
        embed.add_field(name="Cashout", value=f"${money(game.current_payout())}", inline=True)
    else:
        embed.add_field(name="Multiplier", value="1.00x", inline=True)
        embed.add_field(name="Cashout", value="Reveal first", inline=True)

    embed.add_field(name="Balance", value=f"${money(game.balance)}", inline=True)
    return embed


def mines_control_embed(game: MinesGame) -> discord.Embed:
    if game.finished:
        description = game.result or "Game over."
        color = discord.Color.red() if "lose" in description.lower() else discord.Color.green()
    elif game.revealed_safe:
        description = f"Cash out now for ${money(game.current_payout())}."
        color = discord.Color.green()
    else:
        description = "Reveal a safe square before cashing out."
        color = discord.Color.blurple()

    return make_embed("Mines Controls", description, color)


class MinesSession:
    def __init__(self, game: MinesGame) -> None:
        self.game = game
        self.board_message: discord.Message | None = None
        self.control_message: discord.Message | None = None
        self.board_view = MinesBoardView(self)
        self.control_view = MinesControlView(self)

    async def reveal_square(self, interaction: discord.Interaction, index: int) -> None:
        notice = self.game.reveal(index)
        await self.update_after_interaction(interaction, notice)

    async def cash_out(self, interaction: discord.Interaction) -> None:
        notice = self.game.cash_out()
        await self.update_after_interaction(interaction, notice)

    async def update_after_interaction(self, interaction: discord.Interaction, notice: str) -> None:
        await interaction.response.defer()
        try:
            set_balance(self.game.user_id, self.game.balance)
        except BalanceStorageError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return

        if self.game.finished:
            self.finish_session()
        else:
            self.refresh_views()
        await self.edit_messages(notice)

    async def expire(self) -> None:
        if self.game.finished:
            return

        self.game.finished = True
        self.game.result = "Game timed out. Current bet was forfeited."
        try:
            set_balance(self.game.user_id, self.game.balance)
        except BalanceStorageError as error:
            print(f"Could not save timed-out mines game for {self.game.user_id}: {error}")
        self.finish_session()
        await self.edit_messages(self.game.result)

    def refresh_views(self) -> None:
        self.board_view.refresh_buttons()
        self.control_view.refresh_buttons()

    def finish_session(self) -> None:
        active_mines_games.pop(self.game.user_id, None)
        active_mines_sessions.pop(self.game.user_id, None)
        self.refresh_views()
        self.board_view.stop()
        self.control_view.stop()

    async def edit_messages(self, notice: str | None = None) -> None:
        if self.board_message is not None:
            await self.board_message.edit(embed=mines_embed(self.game, notice), view=self.board_view)
        if self.control_message is not None:
            await self.control_message.edit(embed=mines_control_embed(self.game), view=self.control_view)


class MinesSquareButton(discord.ui.Button):
    def __init__(self, session: MinesSession, index: int) -> None:
        self.session = session
        self.index = index
        super().__init__(
            label=str(index + 1),
            style=discord.ButtonStyle.secondary,
            row=index // 5,
        )

    def refresh(self) -> None:
        game = self.session.game
        self.disabled = game.finished or self.index in game.revealed_safe

        if self.index in game.revealed_safe:
            self.label = "Safe"
            self.style = discord.ButtonStyle.success
        elif game.finished and self.index in game.mines:
            self.label = "Mine"
            self.style = discord.ButtonStyle.danger
        elif game.finished:
            self.label = "Safe"
            self.style = discord.ButtonStyle.secondary
        else:
            self.label = str(self.index + 1)
            self.style = discord.ButtonStyle.secondary

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.session.reveal_square(interaction, self.index)


class MinesBoardView(discord.ui.View):
    def __init__(self, session: MinesSession) -> None:
        super().__init__(timeout=180)
        self.session = session
        for index in range(MINES_GRID_SIZE):
            self.add_item(MinesSquareButton(session, index))
        self.refresh_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.session.game.user_id:
            return True

        await interaction.response.send_message("Only the player who started this mines game can use these buttons.", ephemeral=True)
        return False

    async def on_timeout(self) -> None:
        await self.session.expire()

    def refresh_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, MinesSquareButton):
                item.refresh()


class MinesControlView(discord.ui.View):
    def __init__(self, session: MinesSession) -> None:
        super().__init__(timeout=180)
        self.session = session
        self.refresh_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.session.game.user_id:
            return True

        await interaction.response.send_message("Only the player who started this mines game can cash out.", ephemeral=True)
        return False

    async def on_timeout(self) -> None:
        await self.session.expire()

    def refresh_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = self.session.game.finished or not self.session.game.revealed_safe

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.success)
    async def cashout_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.session.cash_out(interaction)


@dataclass
class PlinkoGame:
    user_id: int
    bet: int
    balance: float
    current_path: list[int] = field(default_factory=plinko_path)
    banked_payout: float = 0.0
    balls_dropped: int = 0
    animating: bool = False
    finished: bool = False
    last_bucket: int | None = None
    last_multiplier: float = 0.0
    last_payout: float = 0.0
    result: str = ""

    @classmethod
    def start(cls, user_id: int, balance: float, bet: int) -> "PlinkoGame":
        return cls(user_id=user_id, bet=bet, balance=round(balance, 2))

    def can_add_ball(self) -> bool:
        return not self.finished and not self.animating and self.balance >= self.bet

    def can_cash_out(self) -> bool:
        return not self.finished and not self.animating and self.banked_payout > 0

    def start_ball(self) -> str:
        if self.finished:
            return "This plinko game is already over."
        if self.animating:
            return "The current ball is still dropping."
        if self.balance < self.bet:
            return "Insufficient balance to add another ball."

        self.balance = round(self.balance - self.bet, 2)
        self.current_path = plinko_path()
        self.animating = True
        self.result = f"Dropping ball {self.balls_dropped + 1}."
        return self.result

    def land_ball(self) -> str:
        if not self.animating:
            return self.result or "No ball is dropping."

        self.last_bucket = self.current_path[-1]
        self.last_multiplier = plinko_multiplier(self.last_bucket, self.bet)
        self.last_payout = round(self.bet * self.last_multiplier, 2)
        self.banked_payout = round(self.banked_payout + self.last_payout, 2)
        self.balls_dropped += 1
        self.animating = False
        self.result = (
            f"Ball {self.balls_dropped} landed in bucket {self.last_bucket + 1}: "
            f"${money(self.last_payout)} ({money(self.last_multiplier)}x)."
        )
        return self.result

    def cash_out(self) -> str:
        if self.finished:
            return "This plinko game is already over."
        if self.animating:
            return "The ball is still dropping."
        if self.banked_payout <= 0:
            return "Drop at least one ball before cashing out."

        payout = self.banked_payout
        self.balance = round(self.balance + payout, 2)
        self.finished = True
        profit = round(payout - (self.bet * self.balls_dropped), 2)
        self.result = f"Cashed out for ${money(payout)} after {self.balls_dropped} ball{'s' if self.balls_dropped != 1 else ''}. Profit: ${money(profit)}."
        return self.result


def plinko_embed(game: PlinkoGame, current_row: int | None = None, notice: str | None = None) -> discord.Embed:
    description = notice or game.result or f"Press Add Ball to drop a ${money(game.bet)} ball."
    if game.finished:
        color = discord.Color.green() if game.banked_payout >= game.bet * max(1, game.balls_dropped) else discord.Color.red()
    elif game.animating:
        color = discord.Color.blurple()
    elif game.banked_payout > 0:
        color = discord.Color.gold()
    else:
        color = discord.Color.blurple()

    board = plinko_board(game.current_path, game.bet, current_row)
    embed = make_embed("Plinko", f"{description}\n```text\n{board}\n```", color)
    embed.add_field(name="Bet / Ball", value=f"${money(game.bet)}", inline=True)
    embed.add_field(name="Balls", value=str(game.balls_dropped), inline=True)
    embed.add_field(name="Banked", value=f"${money(game.banked_payout)}", inline=True)

    if game.balls_dropped:
        embed.add_field(name="Last Hit", value=f"${money(game.last_payout)} ({money(game.last_multiplier)}x)", inline=True)
    elif game.animating:
        embed.add_field(name="Last Hit", value="Dropping", inline=True)
    else:
        embed.add_field(name="Last Hit", value="None", inline=True)

    add_ball_state = "Ready" if game.can_add_ball() else "Unavailable"
    cashout_state = "Ready" if game.can_cash_out() else "Unavailable"
    embed.add_field(name="Add Ball", value=add_ball_state, inline=True)
    embed.add_field(name="Cash Out", value=cashout_state, inline=True)
    embed.add_field(name="Balance", value=f"${money(game.balance)}", inline=True)
    return embed


class PlinkoSession:
    def __init__(self, game: PlinkoGame) -> None:
        self.game = game
        self.message: discord.Message | None = None
        self.view = PlinkoView(self)

    async def add_ball(self, interaction: discord.Interaction) -> None:
        if self.game.finished:
            await interaction.response.send_message("This plinko game is already over.", ephemeral=True)
            return
        if self.game.animating:
            await interaction.response.send_message("The current ball is still dropping.", ephemeral=True)
            return
        if self.game.balance < self.game.bet:
            await interaction.response.send_message("Insufficient balance to add another ball.", ephemeral=True)
            return

        await interaction.response.defer()

        previous_balance = self.game.balance
        previous_path = self.game.current_path[:]
        previous_animating = self.game.animating
        previous_result = self.game.result

        notice = self.game.start_ball()
        try:
            set_balance(self.game.user_id, self.game.balance)
        except BalanceStorageError as error:
            self.game.balance = previous_balance
            self.game.current_path = previous_path
            self.game.animating = previous_animating
            self.game.result = previous_result
            await interaction.followup.send(str(error), ephemeral=True)
            return

        self.view.refresh_buttons()
        await self.animate(notice)

    async def animate(self, notice: str | None = None) -> None:
        if self.message is None:
            return

        for row in range(PLINKO_ROWS + 1):
            await self.message.edit(embed=plinko_embed(self.game, row, notice), view=self.view)
            if row < PLINKO_ROWS:
                await asyncio.sleep(PLINKO_ANIMATION_DELAY)

        notice = self.game.land_ball()
        self.view.refresh_buttons()
        await self.message.edit(embed=plinko_embed(self.game, None, notice), view=self.view)

    async def cash_out(self, interaction: discord.Interaction) -> None:
        if self.game.finished:
            await interaction.response.send_message("This plinko game is already over.", ephemeral=True)
            return
        if self.game.animating:
            await interaction.response.send_message("The ball is still dropping.", ephemeral=True)
            return
        if self.game.banked_payout <= 0:
            await interaction.response.send_message("Drop at least one ball before cashing out.", ephemeral=True)
            return

        await interaction.response.defer()

        previous_balance = self.game.balance
        previous_finished = self.game.finished
        previous_result = self.game.result
        notice = self.game.cash_out()
        if self.game.finished:
            try:
                set_balance(self.game.user_id, self.game.balance)
            except BalanceStorageError as error:
                self.game.balance = previous_balance
                self.game.finished = previous_finished
                self.game.result = previous_result
                await interaction.followup.send(str(error), ephemeral=True)
                return

            active_plinko_games.pop(self.game.user_id, None)
            active_plinko_sessions.pop(self.game.user_id, None)
            self.view.disable_buttons()
            self.view.stop()

        if self.message is not None:
            await self.message.edit(embed=plinko_embed(self.game, None, notice), view=self.view)

    async def expire(self) -> None:
        if self.game.finished:
            return

        self.game.finished = True
        if self.game.balls_dropped:
            self.game.result = "Game timed out. Banked plinko payout was forfeited."
        else:
            self.game.result = "Plinko session closed."
        active_plinko_games.pop(self.game.user_id, None)
        active_plinko_sessions.pop(self.game.user_id, None)
        self.view.disable_buttons()
        self.view.stop()

        if self.message is not None:
            await self.message.edit(embed=plinko_embed(self.game, None), view=self.view)


class PlinkoView(discord.ui.View):
    def __init__(self, session: PlinkoSession) -> None:
        super().__init__(timeout=180)
        self.session = session
        self.refresh_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.session.game.user_id:
            return True

        await interaction.response.send_message("Only the player who started this plinko game can cash out.", ephemeral=True)
        return False

    async def on_timeout(self) -> None:
        await self.session.expire()

    def refresh_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.label and item.label.startswith("Add Ball"):
                    item.label = f"Add Ball (${money(self.session.game.bet)})"
                    item.disabled = not self.session.game.can_add_ball()
                elif item.label == "Cash Out":
                    item.disabled = not self.session.game.can_cash_out()

    def disable_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    @discord.ui.button(label="Add Ball", style=discord.ButtonStyle.primary)
    async def add_ball_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.session.add_ball(interaction)

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.success)
    async def cashout_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.session.cash_out(interaction)


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)


@bot.event
async def on_ready() -> None:
    print(f"Casino bot ready as {bot.user}", flush=True)
    log_storage_status()


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot or not message.content.startswith(COMMAND_PREFIX):
        return

    command_name = message.content[len(COMMAND_PREFIX):].split(maxsplit=1)[0].lower()
    if command_name == "dbstatus":
        await bot.process_commands(message)
        return

    if not claim_command_message(message.id):
        print(f"Skipped duplicate command message {message.id}", flush=True)
        return

    await bot.process_commands(message)


@bot.command(name="help")
async def casino_help(ctx: commands.Context) -> None:
    embed = make_embed("Casino Commands", "Use these commands to manage balance and play games.")
    embed.add_field(
        name="Economy",
        value="\n".join(
            [
                ".bal - check your balance",
                ".leaderboard - show top balances",
                ".tip @user [amount] - send balance to another player",
                ".redeem [code] - redeem a promo code",
            ]
        ),
        inline=False,
    )
    embed.add_field(
        name="Play",
        value="\n".join(
            [
                ".bj [bet] - start blackjack with buttons",
                ".cf [bet] [heads/tails] - play coinflip",
                ".dice [bet] [under/over] [target] - play dice",
                ".slots [bet] - spin a slot machine",
                ".plinko [bet] - drop a plinko ball",
                ".mines [bet] [mines] - play a 25-square mines game",
            ]
        ),
        inline=False,
    )
    embed.add_field(
        name="Developer",
        value="\n".join(
            [
                ".bal @user - check another user's balance",
                ".addbal @user [amount] - add balance",
                ".removebal @user [amount] - remove balance",
                ".promo [code] [amount] [people_limit] - create a promo code",
                ".testhand [cards] - set active blackjack hand; example: .testhand A K",
                ".dbstatus - check Supabase connection",
            ]
        ),
        inline=False,
    )
    await ctx.send(embed=embed)


@bot.command(name="bal")
async def balance_command(ctx: commands.Context, target: discord.Member = None) -> None:
    if target is not None and not await require_developer_role(ctx):
        return

    member = target or ctx.author
    balance = balance_for(member.id)
    if target is None:
        description = f"Your balance is ${money(balance)}."
    else:
        description = f"{member.mention}'s balance is ${money(balance)}."

    await ctx.send(embed=make_embed("Balance", description))


@bot.command(name="leaderboard", aliases=["lb"])
async def leaderboard_command(ctx: commands.Context) -> None:
    leaderboard = leaderboard_balances()
    if not leaderboard:
        await ctx.send(embed=make_embed("Leaderboard", "No balances yet."))
        return

    lines = [
        f"{rank}. <@{user_id}> - ${money(balance)}"
        for rank, (user_id, balance) in enumerate(leaderboard, start=1)
    ]
    await ctx.send(embed=make_embed("Leaderboard", "\n".join(lines), discord.Color.gold()))


@bot.command(name="dbstatus")
@commands.guild_only()
@developer_only()
async def dbstatus_command(ctx: commands.Context) -> None:
    if not await require_developer_role(ctx):
        return

    lines = [
        f"Balance storage: {'Supabase' if supabase_enabled() else 'memory'}",
        f"SUPABASE_URL set: {'yes' if SUPABASE_URL else 'no'}",
        f"SUPABASE_SERVICE_ROLE_KEY set: {'yes' if SUPABASE_SERVICE_ROLE_KEY else 'no'}",
        f"SUPABASE_ANON_KEY fallback set: {'yes' if SUPABASE_ANON_KEY else 'no'}",
        f"Balances table: `{SUPABASE_BALANCES_TABLE}`",
        f"Command guard table: `{SUPABASE_COMMAND_MESSAGES_TABLE}`",
    ]

    if supabase_enabled():
        try:
            rows = supabase_request(
                "GET",
                SUPABASE_BALANCES_TABLE,
                query={"select": "user_id,balance", "limit": "1"},
            )
        except HTTPError as error:
            lines.append(f"Supabase test: failed with {http_error_summary(error)}")
        except (URLError, TimeoutError, OSError, ValueError) as error:
            lines.append(f"Supabase test: failed ({type(error).__name__})")
        else:
            row_count = len(rows) if isinstance(rows, list) else 0
            lines.append(f"Supabase test: connected ({row_count} sample row{'s' if row_count != 1 else ''})")

        try:
            guard_rows = supabase_request(
                "GET",
                SUPABASE_COMMAND_MESSAGES_TABLE,
                query={"select": "message_id", "limit": "1"},
            )
        except HTTPError as error:
            lines.append(f"Command guard table: failed with {http_error_summary(error)}")
        except (URLError, TimeoutError, OSError, ValueError) as error:
            lines.append(f"Command guard table: failed ({type(error).__name__})")
        else:
            row_count = len(guard_rows) if isinstance(guard_rows, list) else 0
            lines.append(f"Command guard table: connected ({row_count} sample row{'s' if row_count != 1 else ''})")
    else:
        lines.append("Supabase test: skipped because env vars are missing")

    await ctx.send(embed=make_embed("Database Status", "\n".join(lines)))


@bot.command(name="tip")
@commands.guild_only()
async def tip_command(ctx: commands.Context, target: discord.Member, amount: int) -> None:
    if amount < 1:
        await ctx.send("Please enter a positive integer amount.")
        return

    if target.bot:
        await ctx.send("You cannot tip bots.")
        return

    if target.id == ctx.author.id:
        await ctx.send("You cannot tip yourself.")
        return

    sender_active_game = active_game_name(ctx.author.id)
    if sender_active_game is not None:
        await ctx.send(f"Finish your active {sender_active_game} game before sending a tip.")
        return

    sender_balance = balance_for(ctx.author.id)
    if amount > sender_balance:
        await ctx.send("You do not have enough balance for that tip.")
        return

    target_balance = balance_for(target.id)
    sender_balance = round(sender_balance - amount, 2)
    target_balance = round(target_balance + amount, 2)
    set_balance(ctx.author.id, sender_balance)
    set_balance(target.id, target_balance)

    await ctx.send(
        embed=make_embed(
            "Tip Sent",
            f"{ctx.author.mention} sent ${money(amount)} to {target.mention}.\n"
            f"Your new balance: ${money(sender_balance)}.",
            discord.Color.green(),
        )
    )


@bot.command(name="addbal")
@commands.guild_only()
@developer_only()
async def add_balance_command(ctx: commands.Context, target: discord.Member, amount: int) -> None:
    if not await require_developer_role(ctx):
        return

    if amount < 1:
        await ctx.send("Please enter a positive integer amount.")
        return

    balance = balance_for(target.id) + amount
    set_balance(target.id, balance)
    await ctx.send(
        embed=make_embed(
            "Balance Updated",
            f"Added ${money(amount)} to {target.mention}.\nNew balance: ${money(balance)}.",
        )
    )


@bot.command(name="removebal")
@commands.guild_only()
@developer_only()
async def remove_balance_command(ctx: commands.Context, target: discord.Member, amount: int) -> None:
    if not await require_developer_role(ctx):
        return

    if amount < 1:
        await ctx.send("Please enter a positive integer amount.")
        return

    balance = max(0, balance_for(target.id) - amount)
    set_balance(target.id, balance)
    await ctx.send(
        embed=make_embed(
            "Balance Updated",
            f"Removed ${money(amount)} from {target.mention}.\nNew balance: ${money(balance)}.",
        )
    )


@bot.command(name="promo")
@commands.guild_only()
@developer_only()
async def promo_command(ctx: commands.Context, code: str, amount: int, people_limit: int = 1) -> None:
    if not await require_developer_role(ctx):
        return

    normalized_code = normalize_promo_code(code)
    if not valid_promo_code(normalized_code):
        await ctx.send("Promo codes must be 3-32 characters using letters, numbers, hyphens, or underscores.")
        return

    if amount < 1:
        await ctx.send("Promo amount must be at least 1.")
        return

    if people_limit < 1:
        await ctx.send("Promo people limit must be at least 1.")
        return

    promo_codes[normalized_code] = PromoCode(code=normalized_code, amount=amount, max_people=people_limit)
    await ctx.send(
        embed=make_embed(
            "Promo Created",
            f"Code `{normalized_code}` gives ${money(amount)} and can be redeemed by {people_limit} unique user{'s' if people_limit != 1 else ''}.",
        )
    )


@bot.command(name="redeem")
async def redeem_command(ctx: commands.Context, code: str) -> None:
    active_game = active_game_name(ctx.author.id)
    if active_game is not None:
        await ctx.send(f"Finish your active {active_game} game before redeeming a promo code.")
        return

    normalized_code = normalize_promo_code(code)
    promo = promo_codes.get(normalized_code)
    if promo is None:
        await ctx.send("That promo code does not exist.")
        return

    can_redeem, reason = promo.can_redeem(ctx.author.id)
    if not can_redeem:
        await ctx.send(reason)
        return

    promo.redeemed_by.add(ctx.author.id)
    balance = balance_for(ctx.author.id) + promo.amount
    set_balance(ctx.author.id, balance)
    await ctx.send(
        embed=make_embed(
            "Promo Redeemed",
            f"Redeemed `{promo.code}` for ${money(promo.amount)}.\n"
            f"New balance: ${money(balance)}.\n"
            f"Remaining redemptions: {promo.people_left}.",
            discord.Color.green(),
        )
    )


@bot.command(name="cf")
async def coinflip_command(ctx: commands.Context, requested_bet: int, side: str) -> None:
    active_game = active_game_name(ctx.author.id)
    if active_game is not None:
        await ctx.send(f"Finish your active {active_game} game before starting another game.")
        return

    side = side.lower()
    if side not in ("heads", "tails"):
        await ctx.send("Please choose heads or tails.")
        return

    balance = balance_for(ctx.author.id)
    bet, warning = normalized_bet(balance, requested_bet)
    if bet is None:
        await ctx.send(warning)
        return

    balance = round(balance - bet, 2)
    won = random.random() < COINFLIP_WIN_RATE
    if won:
        result = side
    else:
        result = "tails" if side == "heads" else "heads"

    if won:
        payout = round(even_money_win_return(bet) * bet, 2)
        balance = round(balance + payout, 2)
        outcome = f"{result.title()}! You win."
        color = discord.Color.green()
    else:
        outcome = f"{result.title()}! You lose."
        color = discord.Color.red()

    set_balance(ctx.author.id, balance)
    description = f"{warning + chr(10) if warning else ''}{outcome}\nNew balance: ${money(balance)}."
    await ctx.send(embed=make_embed("Coinflip", description, color))


@bot.command(name="dice")
async def dice_command(ctx: commands.Context, requested_bet: int, direction: str, target: float) -> None:
    active_game = active_game_name(ctx.author.id)
    if active_game is not None:
        await ctx.send(f"Finish your active {active_game} game before starting another game.")
        return

    direction = direction.lower()
    if direction not in ("under", "over"):
        await ctx.send("Choose under or over.")
        return

    if target <= 0 or target >= 100:
        await ctx.send("Target must be between 0 and 100.")
        return

    win_chance = target if direction == "under" else 100 - target
    if win_chance < 1 or win_chance > 98:
        await ctx.send("Target must give a win chance from 1% to 98%.")
        return

    balance = balance_for(ctx.author.id)
    bet, warning = normalized_bet(balance, requested_bet)
    if bet is None:
        await ctx.send(warning)
        return

    multiplier = 100 * bet_return_rate(bet) / win_chance
    roll = random.randint(0, 9999) / 100
    won = roll < target if direction == "under" else roll > target

    balance = round(balance - bet, 2)
    payout = round(bet * multiplier, 2)
    profit = round(payout - bet, 2)

    if won:
        balance = round(balance + payout, 2)
        outcome = f"You win! Profit: ${money(profit)}."
        color = discord.Color.green()
    else:
        outcome = f"You lose. Profit: -${money(bet)}."
        color = discord.Color.red()

    set_balance(ctx.author.id, balance)
    lines = [
        warning,
        f"Dice roll: {money(roll)}",
        f"Mode: roll {direction} {money(target)}",
        f"Win chance: {money(win_chance)}%",
        f"Multiplier: {money(multiplier)}x",
        outcome,
        f"New balance: ${money(balance)}.",
    ]
    await ctx.send(embed=make_embed("Dice", "\n".join(line for line in lines if line), color))


@bot.command(name="slots", aliases=["slot"])
async def slots_command(ctx: commands.Context, requested_bet: int) -> None:
    active_game = active_game_name(ctx.author.id)
    if active_game is not None:
        await ctx.send(f"Finish your active {active_game} game before starting another game.")
        return

    balance = balance_for(ctx.author.id)
    bet, warning = normalized_bet(balance, requested_bet)
    if bet is None:
        await ctx.send(warning)
        return

    reels = spin_slots()
    base_multiplier = slots_multiplier(reels)
    multiplier = round(base_multiplier * bet_return_rate(bet) * SLOT_RETURN_MULTIPLIER, 4)
    payout = round(bet * multiplier, 2)

    balance = round(balance - bet + payout, 2)
    set_balance(ctx.author.id, balance)

    if payout > 0:
        profit = round(payout - bet, 2)
        outcome = f"You win ${money(payout)}. Profit: ${money(profit)}."
        color = discord.Color.green()
    else:
        outcome = f"No match. You lose ${money(bet)}."
        color = discord.Color.red()

    lines = [
        warning,
        f"[ {slots_display(reels)} ]",
        f"Multiplier: {money(multiplier)}x",
        outcome,
        f"New balance: ${money(balance)}.",
    ]
    await ctx.send(embed=make_embed("Slots", "\n".join(line for line in lines if line), color))


@bot.command(name="plinko")
async def plinko_command(ctx: commands.Context, requested_bet: int) -> None:
    active_game = active_game_name(ctx.author.id)
    if active_game is not None:
        await ctx.send(f"Finish your active {active_game} game before starting plinko.")
        return

    balance = balance_for(ctx.author.id)
    bet, warning = normalized_bet(balance, requested_bet)
    if bet is None:
        await ctx.send(warning)
        return

    game = PlinkoGame.start(ctx.author.id, balance, bet)
    active_plinko_games[ctx.author.id] = game

    session = PlinkoSession(game)
    active_plinko_sessions[ctx.author.id] = session
    notice = f"{warning + ' ' if warning else ''}Plinko started. Press Add Ball to drop a ${money(bet)} ball."
    session.message = await ctx.send(embed=plinko_embed(game, None, notice), view=session.view)


@bot.command(name="mines")
async def mines_command(ctx: commands.Context, requested_bet: int, mine_count: int) -> None:
    active_game = active_game_name(ctx.author.id)
    if active_game is not None:
        await ctx.send(f"Finish your active {active_game} game before starting mines.")
        return

    if mine_count < 1 or mine_count >= MINES_GRID_SIZE:
        await ctx.send(f"Mine count must be from 1 to {MINES_GRID_SIZE - 1}.")
        return

    balance = balance_for(ctx.author.id)
    bet, warning = normalized_bet(balance, requested_bet)
    if bet is None:
        await ctx.send(warning)
        return

    game = MinesGame.start(ctx.author.id, balance, bet, mine_count)
    set_balance(ctx.author.id, game.balance)
    active_mines_games[ctx.author.id] = game

    session = MinesSession(game)
    active_mines_sessions[ctx.author.id] = session
    notice = f"{warning + ' ' if warning else ''}Game started, bet is ${money(bet)}."
    session.board_message = await ctx.send(embed=mines_embed(game, notice), view=session.board_view)
    session.control_message = await ctx.send(embed=mines_control_embed(game), view=session.control_view)


@bot.command(name="testhand")
@commands.guild_only()
@developer_only()
async def test_hand_command(ctx: commands.Context, *card_labels: str) -> None:
    if not await require_developer_role(ctx):
        return

    if not card_labels:
        await ctx.send("Use `.testhand A K` or `.testhand 10 9 2` while you have an active blackjack hand.")
        return

    cards = parse_cards(card_labels)
    if cards is None:
        await ctx.send("Cards must be A, 2-10, J, Q, or K. Example: `.testhand A K`.")
        return

    game = active_blackjack_games.get(ctx.author.id)
    if game is None:
        await ctx.send("Start a blackjack game with `.bj [bet]` before using `.testhand`.")
        return

    notice = game.test_hand(cards)
    set_balance(ctx.author.id, game.balance)

    view = active_blackjack_views.get(ctx.author.id)
    if view is not None:
        if game.finished:
            active_blackjack_games.pop(ctx.author.id, None)
            active_blackjack_views.pop(ctx.author.id, None)
            view.disable_buttons()
        else:
            view.refresh_buttons()

        if view.message is not None:
            await view.message.edit(embed=blackjack_embed(game, notice), view=view)

    await ctx.send(notice)


@bot.command(name="bj")
async def blackjack_command(ctx: commands.Context, requested_bet: int) -> None:
    active_game = active_game_name(ctx.author.id)
    if active_game is not None:
        await ctx.send(f"Finish your active {active_game} game before starting blackjack.")
        return

    balance = balance_for(ctx.author.id)
    bet, warning = normalized_bet(balance, requested_bet)
    if bet is None:
        await ctx.send(warning)
        return

    game = BlackjackGame.start(ctx.author.id, balance, bet)
    set_balance(ctx.author.id, game.balance)
    active_blackjack_games[ctx.author.id] = game

    view = BlackjackView(game)
    active_blackjack_views[ctx.author.id] = view
    notice = f"{warning + ' ' if warning else ''}Game started, bet is ${money(bet)}."
    view.message = await ctx.send(embed=blackjack_embed(game, notice), view=view)


def command_usage(command_name: str | None) -> str | None:
    usages = {
        "bal": ".bal [@user]",
        "leaderboard": ".leaderboard",
        "lb": ".leaderboard",
        "tip": ".tip @user amount",
        "dbstatus": ".dbstatus",
        "addbal": ".addbal @user amount",
        "removebal": ".removebal @user amount",
        "promo": ".promo code amount [people_limit]",
        "redeem": ".redeem code",
        "mines": ".mines bet mines",
        "cf": ".cf bet heads",
        "dice": ".dice bet under target",
        "slots": ".slots bet",
        "slot": ".slots bet",
        "plinko": ".plinko bet",
        "bj": ".bj bet",
        "testhand": ".testhand A K",
    }
    return usages.get(command_name or "")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    original_error = getattr(error, "original", None)
    if isinstance(error, BalanceStorageError):
        await ctx.send(str(error))
    elif isinstance(original_error, BalanceStorageError):
        await ctx.send(str(original_error))
    elif isinstance(error, commands.MissingRequiredArgument):
        usage = command_usage(ctx.command.name if ctx.command else None)
        if usage is not None:
            await ctx.send(f"Use `{usage}`.")
        else:
            await ctx.send("Missing command argument. Use `.help` to see the command format.")
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("That command can only be used in a server.")
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("You need the developer role to use that command.")
    elif isinstance(error, commands.BadArgument):
        usage = command_usage(ctx.command.name if ctx.command else None)
        if usage is not None:
            await ctx.send(f"Use `{usage}`.")
        else:
            await ctx.send("Please check your command numbers and try again.")
    elif isinstance(error, commands.CommandNotFound):
        return
    else:
        raise error


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Set the DISCORD_TOKEN environment variable before running the bot.")
    start_render_health_server()
    log_storage_status()
    bot.run(token)


if __name__ == "__main__":
    main()
