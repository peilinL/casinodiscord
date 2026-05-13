from __future__ import annotations

import os
import random
from dataclasses import dataclass, field


try:
    import certifi
except ImportError:
    certifi = None
else:
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import discord
from discord.ext import commands


STARTING_BALANCE = 1000
COMMAND_PREFIX = "."
DEVELOPER_ROLE_NAME = "developer"
COINFLIP_WIN_RATE = 0.45

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
active_blackjack_games: dict[int, "BlackjackGame"] = {}
active_blackjack_views: dict[int, "BlackjackView"] = {}


def money(amount: float) -> str:
    return f"{amount:.2f}"


def balance_for(user_id: int) -> float:
    if user_id not in balances:
        balances[user_id] = float(STARTING_BALANCE)
    return balances[user_id]


def set_balance(user_id: int, amount: float) -> None:
    balances[user_id] = round(amount, 2)


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
            self.balance = round(self.balance + 2 * self.bet, 2)
            outcome = "Dealer busts. You win."
        elif dealer_total > player_total:
            outcome = "You lost."
        elif dealer_total < player_total:
            self.balance = round(self.balance + 2 * self.bet, 2)
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
                self.balance = round(self.balance + 2 * bet, 2)
                results.append(f"Dealer busts. Hand {index + 1} wins.")
            elif dealer_total > total:
                results.append(f"Hand {index + 1} lost.")
            elif dealer_total < total:
                self.balance = round(self.balance + 2 * bet, 2)
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
        set_balance(self.game.user_id, self.game.balance)
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

    async def update_game(self, interaction: discord.Interaction, notice: str) -> None:
        set_balance(self.game.user_id, self.game.balance)
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


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)


@bot.event
async def on_ready() -> None:
    print(f"Casino bot ready as {bot.user}")


@bot.command(name="help")
async def casino_help(ctx: commands.Context) -> None:
    embed = make_embed(
        "Casino Commands",
        "\n".join(
            [
                ".bal - check your balance",
                ".bj [bet] - start blackjack with buttons",
                ".cf [bet] [heads/tails] - play coinflip",
                ".dice [bet] [under/over] [target] - play dice with a 1% house edge",
                ".addbal @user [amount] - developer role only",
                ".removebal @user [amount] - developer role only",
                ".testhand [cards] - developer role only; example: .testhand A K",
            ]
        ),
    )
    await ctx.send(embed=embed)


@bot.command(name="bal")
async def balance_command(ctx: commands.Context) -> None:
    balance = balance_for(ctx.author.id)
    await ctx.send(embed=make_embed("Balance", f"Your balance is ${money(balance)}."))


@bot.command(name="addbal")
@commands.guild_only()
@developer_only()
async def add_balance_command(ctx: commands.Context, target: discord.Member, amount: int) -> None:
    if not await require_developer_role(ctx):
        return

    if has_active_blackjack(target.id):
        await ctx.send(f"{target.mention} has an active blackjack hand. Wait until it finishes before changing their balance.")
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

    if has_active_blackjack(target.id):
        await ctx.send(f"{target.mention} has an active blackjack hand. Wait until it finishes before changing their balance.")
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


@bot.command(name="cf")
async def coinflip_command(ctx: commands.Context, requested_bet: int, side: str) -> None:
    if has_active_blackjack(ctx.author.id):
        await ctx.send("Finish your active blackjack hand before starting another game.")
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
        balance = round(balance + 2 * bet, 2)
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
    if has_active_blackjack(ctx.author.id):
        await ctx.send("Finish your active blackjack hand before starting another game.")
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

    multiplier = 99 / win_chance
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
    if ctx.author.id in active_blackjack_games:
        await ctx.send("You already have an active blackjack hand.")
        return

    balance = balance_for(ctx.author.id)
    bet, warning = normalized_bet(balance, requested_bet)
    if bet is None:
        await ctx.send(warning)
        return

    game = BlackjackGame.start(ctx.author.id, balance, bet)
    active_blackjack_games[ctx.author.id] = game
    set_balance(ctx.author.id, game.balance)

    view = BlackjackView(game)
    active_blackjack_views[ctx.author.id] = view
    notice = f"{warning + ' ' if warning else ''}Game started, bet is ${money(bet)}."
    view.message = await ctx.send(embed=blackjack_embed(game, notice), view=view)


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.MissingRequiredArgument):
        if ctx.command and ctx.command.name in ("addbal", "removebal"):
            await ctx.send(f"Use `.{ctx.command.name} @user amount`.")
        else:
            await ctx.send("Missing command argument. Use `.help` to see the command format.")
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("That command can only be used in a server.")
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("You need the developer role to use that command.")
    elif isinstance(error, commands.BadArgument):
        if ctx.command and ctx.command.name in ("addbal", "removebal"):
            await ctx.send(f"Use `.{ctx.command.name} @user amount`.")
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
    bot.run(token)


if __name__ == "__main__":
    main()
