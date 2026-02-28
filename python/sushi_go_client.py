#!/usr/bin/env python3
"""
Sushi Go Client - Python Starter Kit

This client connects to the Sushi Go server and plays using a simple strategy.
Modify the `choose_card` method to implement your own AI!

Usage:
    python sushi_go_client.py <server_host> <server_port> <game_id> <player_name>

Example:
    python sushi_go_client.py localhost 7878 abc123 MyBot
"""

import random
import re
import socket
import sys
from dataclasses import dataclass
from typing import Optional

# Card names used by the protocol (now using full names instead of codes)
CARD_NAMES = {
    "Tempura": "Tempura",
    "Sashimi": "Sashimi",
    "Dumpling": "Dumpling",
    "Maki Roll (1)": "Maki Roll (1)",
    "Maki Roll (2)": "Maki Roll (2)",
    "Maki Roll (3)": "Maki Roll (3)",
    "Egg Nigiri": "Egg Nigiri",
    "Salmon Nigiri": "Salmon Nigiri",
    "Squid Nigiri": "Squid Nigiri",
    "Pudding": "Pudding",
    "Wasabi": "Wasabi",
    "Chopsticks": "Chopsticks",
}


@dataclass
class GameState:
    """Tracks the current state of the game."""

    game_id: str
    player_id: int
    hand: list[str]
    round: int = 1
    turn: int = 1
    played_cards: list[str] = None
    has_chopsticks: bool = False
    has_unused_wasabi: bool = False
<<<<<<< ours
    puddings: int = 0           # our cumulative puddings across all rounds
    player_count: int = 2       # total players in the game
    opponent_maki: dict = None  # player_name -> maki pip total this round
    opponent_puddings: dict = None  # player_name -> cumulative puddings
=======
    puddings: int = 0
    player_count: int = 0
>>>>>>> theirs

    def __post_init__(self):
        if self.played_cards is None:
            self.played_cards = []
        if self.opponent_maki is None:
            self.opponent_maki = {}
        if self.opponent_puddings is None:
            self.opponent_puddings = {}


class SushiGoClient:
    """A client for playing Sushi Go."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self.state: Optional[GameState] = None
        self._recv_buffer = ""

    def connect(self):
        """Connect to the server."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        self._recv_buffer = ""
        print(f"Connected to {self.host}:{self.port}")

    def disconnect(self):
        """Disconnect from the server."""
        if self.sock:
            self.sock.close()
            self.sock = None

    def send(self, command: str):
        """Send a command to the server."""
        message = command + "\n"
        self.sock.sendall(message.encode("utf-8"))
        print(f">>> {command}")

    def receive(self) -> str:
        """Receive one line-delimited message from the server."""
        while True:
            if "\n" in self._recv_buffer:
                line, self._recv_buffer = self._recv_buffer.split("\n", 1)
                message = line.strip()
                print(f"<<< {message}")
                return message

            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Server closed connection")
            self._recv_buffer += chunk.decode("utf-8", errors="replace")

    def receive_until(self, predicate) -> str:
        """Read lines until one matches predicate."""
        while True:
            message = self.receive()
            if not message:
                continue
            if predicate(message):
                return message

    def join_game(self, game_id: str, player_name: str) -> bool:
        """Join a game."""
        self.send(f"JOIN {game_id} {player_name}")
        response = self.receive_until(
            lambda line: line.startswith("WELCOME") or line.startswith("ERROR")
        )

        if response.startswith("WELCOME"):
            parts = response.split()
            self.state = GameState(game_id=parts[1], player_id=int(parts[2]), hand=[])
            return True
        elif response.startswith("ERROR"):
            print(f"Failed to join: {response}")
            return False
        return False

    def signal_ready(self):
        """Signal that we're ready to start."""
        self.send("READY")
        return self.receive()

    def play_card(self, card_index: int):
        """Play a card by index."""
        self.send(f"PLAY {card_index}")
        return self.receive()

    def play_chopsticks(self, index1: int, index2: int):
        """Use chopsticks to play two cards."""
        self.send(f"CHOPSTICKS {index1} {index2}")
        return self.receive()

    def parse_hand(self, message: str):
        """Parse a HAND message and update state."""
        if message.startswith("HAND"):
            payload = message[len("HAND ") :]
            cards = []
            for match in re.finditer(r"(\d+):(.*?)(?=\s\d+:|$)", payload):
                cards.append(match.group(2).strip())
            if self.state:
                self.state.hand = cards
                # Update chopsticks/wasabi tracking based on played cards
                self.state.has_chopsticks = "Chopsticks" in self.state.played_cards
                wasabi_count = self.state.played_cards.count("Wasabi")
                nigiri_count = sum(1 for c in self.state.played_cards if "Nigiri" in c)
                self.state.has_unused_wasabi = wasabi_count > nigiri_count

<<<<<<< ours
    def _parse_played(self, message: str):
        """Parse a PLAYED message to track opponent maki and puddings."""
        # Format: PLAYED Sarah:Tempura; Ashley:Maki Roll (2)
        if not self.state:
            return
        payload = message[len("PLAYED "):]
        for entry in payload.split("; "):
            if ":" not in entry:
                continue
            colon_idx = entry.index(":")
            player = entry[:colon_idx].strip()
            card = entry[colon_idx + 1:].strip()
            if "Maki Roll" in card:
                m = re.search(r"\((\d+)\)", card)
                if m:
                    self.state.opponent_maki[player] = (
                        self.state.opponent_maki.get(player, 0) + int(m.group(1))
                    )
            if card == "Pudding":
                self.state.opponent_puddings[player] = (
                    self.state.opponent_puddings.get(player, 0) + 1
                )

    def _score_card(self, card: str, hand: list[str]) -> float:
        """
        Score a single card given the current hand and game state.
        Higher score = better pick.
        """
        if not self.state:
            return 0.0

        played = self.state.played_cards or []
        sashimi_count = played.count("Sashimi") % 3
        tempura_count = played.count("Tempura") % 2
        dumpling_count = played.count("Dumpling")
        has_wasabi = self.state.has_unused_wasabi

        our_maki = sum(
            int(re.search(r"\((\d+)\)", c).group(1))
            for c in played
            if "Maki Roll" in c
        )

        # Turns remaining this round (hand sizes: 2p=10, 3p=9, 4p=8, 5p=7)
        hand_size_at_start = max(12 - self.state.player_count, 7)
        turns_left = hand_size_at_start - (self.state.turn - 1)

        def maki_urgency() -> float:
            max_opp = max(self.state.opponent_maki.values(), default=0)
            gap = max_opp - our_maki
            if gap >= 3:
                return 3.5   # Falling behind — must catch up
            elif gap >= 0:
                return 2.5   # Competitive
            return 1.2       # Already ahead

        def pudding_value() -> float:
            our_puds = self.state.puddings + sum(
                1 for c in played if c == "Pudding"
            )
            opp_puds = list(self.state.opponent_puddings.values())
            min_opp = min(opp_puds, default=0)
            max_opp = max(opp_puds, default=0)
            round_mult = {1: 0.8, 2: 1.1, 3: 1.8}.get(self.state.round, 1.0)
            if our_puds <= min_opp:
                return 3.5 * round_mult   # Risk of losing pudding penalty
            elif our_puds < max_opp:
                return 2.0 * round_mult
            return 1.2 * round_mult
=======
    def _card_value(self, card: str, turns_left: int) -> float:
        """Score a single card given the current game state."""
        played = self.state.played_cards if self.state else []
        current_round = self.state.round if self.state else 1

        tempura_count = played.count("Tempura")
        sashimi_count = played.count("Sashimi")
        dumpling_count = played.count("Dumpling")
        maki_total = sum(int(c[-2]) for c in played if c.startswith("Maki Roll"))
        wasabi_count = played.count("Wasabi")
        nigiri_count = sum(1 for c in played if "Nigiri" in c)
        has_unused_wasabi = wasabi_count > nigiri_count
>>>>>>> theirs

        # --- Nigiri: guaranteed points, tripled by wasabi ---
        if card == "Squid Nigiri":
<<<<<<< ours
            return 9.0 if has_wasabi else 3.0

        elif card == "Salmon Nigiri":
            return 6.0 if has_wasabi else 2.0

        elif card == "Egg Nigiri":
            return 3.0 if has_wasabi else 1.0

        elif card == "Wasabi":
            if "Squid Nigiri" in hand:
                return 7.0   # Immediate 9-pt combo
            elif "Salmon Nigiri" in hand:
                return 5.0   # Immediate 6-pt combo
            elif turns_left >= 3:
                return 4.5   # Good speculative play
            elif turns_left >= 2:
                return 2.5
            return 0.8

        elif card == "Tempura":
            if tempura_count == 1:
                return 5.0   # Completes pair for 5 pts
            elif turns_left >= 2:
                return 2.5   # Half a pair — still worthwhile
            return 0.3

        elif card == "Sashimi":
            if sashimi_count == 2:
                return 10.0  # Completes set for 10 pts
            elif sashimi_count == 1:
                return 5.0 if turns_left >= 1 else 0.0
            else:
                return 3.3 if turns_left >= 3 else 0.0

        elif card == "Dumpling":
            # Marginal gain grows: 1st=1pt gap, 2nd=2pt gap, ..., 5th=5pt gap
            return float(min(dumpling_count + 1, 5))

        elif card == "Maki Roll (3)":
            return maki_urgency() * 1.5

        elif card == "Maki Roll (2)":
            return maki_urgency() * 1.0

        elif card == "Maki Roll (1)":
            return maki_urgency() * 0.5

        elif card == "Pudding":
            return pudding_value()

        elif card == "Chopsticks":
            high_val = sum(
                1 for c in hand
                if c in ("Squid Nigiri", "Salmon Nigiri", "Sashimi",
                         "Tempura", "Dumpling", "Wasabi")
            )
            return 2.5 if high_val >= 3 and turns_left >= 3 else 0.5
=======
            return 9.0 if has_unused_wasabi else 3.0
        if card == "Salmon Nigiri":
            return 6.0 if has_unused_wasabi else 2.0
        if card == "Egg Nigiri":
            return 3.0 if has_unused_wasabi else 1.0

        # --- Wasabi: multiplier for NEXT nigiri ---
        if card == "Wasabi":
            if has_unused_wasabi:
                return 0.1  # Already have one unused; stacking is wasteful
            if turns_left >= 1:
                return 2.5  # Expected bonus from tripling a future nigiri
            return 0.0

        # --- Tempura: 5 pts per pair ---
        if card == "Tempura":
            if tempura_count % 2 == 1:  # Completes a pair right now
                return 5.0
            return 2.5 if turns_left >= 1 else 0.0

        # --- Sashimi: 10 pts per set of 3 ---
        if card == "Sashimi":
            mod = sashimi_count % 3
            if mod == 2:  # Completes a set right now
                return 10.0
            if mod == 1:  # Need 1 more after this
                return 3.5 if turns_left >= 1 else 0.0
            return 3.5 if turns_left >= 2 else 0.0  # Starting fresh, need 2 more

        # --- Dumpling: marginal value 1/2/3/4/5/0 for 1st–6th ---
        if card == "Dumpling":
            marginals = [1, 2, 3, 4, 5, 0]
            return float(marginals[min(dumpling_count, 5)])

        # --- Maki Rolls: competitive (~1 pt per symbol + commitment bonus) ---
        if card.startswith("Maki Roll"):
            symbols = int(card[-2])
            if maki_total == 0 and turns_left == 0:
                return 0.3  # Too late to start competing
            return symbols * 1.0 + (0.5 if maki_total > 0 else 0.0)

        # --- Pudding: end-of-game +6 for most; -6 for fewest (not in 2-player) ---
        if card == "Pudding":
            if self.state and self.state.player_count == 2:
                # 2-player rule: only +6 for most puddings, no penalty for fewest
                return 1.0 + (current_round - 1) * 0.5
            # Multiplayer: both +6 and -6 at stake, higher value
            return 1.5 + (current_round - 1) * 0.5

        # --- Chopsticks: lets us play 2 cards in a future turn ---
        if card == "Chopsticks":
            return 1.0 if turns_left >= 2 else 0.0
>>>>>>> theirs

        return 0.0

    def choose_card(self, hand: list[str]) -> int:
        """
        Choose the highest-scoring card to play.

        Args:
            hand: List of card names in your current hand

        Returns:
            Index of the card to play (0-based)
        """
        if not self.state or not hand:
            return 0

        turns_left = len(hand) - 1
        scores = [(self._card_value(c, turns_left), i) for i, c in enumerate(hand)]
        return max(scores)[1]

    def handle_message(self, message: str):
        """Handle a message from the server."""
        if message.startswith("HAND"):
            self.parse_hand(message)
        elif message.startswith("GAME_START"):
            parts = message.split()
            if self.state and len(parts) > 1:
                self.state.player_count = int(parts[1])
        elif message.startswith("ROUND_START"):
            parts = message.split()
            if self.state:
                self.state.round = int(parts[1])
                self.state.turn = 1
                self.state.played_cards = []
                self.state.opponent_maki = {}
        elif message.startswith("PLAYED"):
            if self.state:
                self.state.turn += 1
                self._parse_played(message)
        elif message.startswith("ROUND_END"):
            if self.state:
                # Persist our puddings before clearing the round's played cards
                self.state.puddings += sum(
                    1 for c in self.state.played_cards if c == "Pudding"
                )
                self.state.played_cards = []
                self.state.opponent_maki = {}
        elif message.startswith("GAME_END"):
            print("Game over!")
            return False
        elif message.startswith("WAITING"):
            pass
        return True

    def play_turn(self):
        """Play a single turn, using chopsticks when strategically worthwhile."""
        if not self.state or not self.state.hand:
            return

        hand = self.state.hand
        turns_left = len(hand) - 1

<<<<<<< ours
        # Attempt chopsticks play when the two best cards together score highly
        if self.state.has_chopsticks and len(hand) >= 2:
            idx1 = self.choose_card(hand)
            remaining = [i for i in range(len(hand)) if i != idx1]
            idx2 = max(remaining, key=lambda i: self._score_card(hand[i], hand))

            score1 = self._score_card(hand[idx1], hand)
            score2 = self._score_card(hand[idx2], hand)

            if score1 + score2 >= 8.0:
                response = self.play_chopsticks(idx1, idx2)
                if response.startswith("OK") and self.state:
                    self.state.played_cards.append(hand[idx1])
                    self.state.played_cards.append(hand[idx2])
                return

        # Normal single-card play
=======
        # Use chopsticks if available and the second-best card is worthwhile
        if self.state.has_chopsticks and len(hand) >= 2:
            scored = sorted(
                [(self._card_value(c, turns_left), i) for i, c in enumerate(hand)],
                reverse=True,
            )
            if scored[1][0] >= 1.0:
                idx1, idx2 = scored[0][1], scored[1][1]
                response = self.play_chopsticks(idx1, idx2)
                if response.startswith("OK"):
                    card1, card2 = hand[idx1], hand[idx2]
                    if "Chopsticks" in self.state.played_cards:
                        self.state.played_cards.remove("Chopsticks")
                    self.state.played_cards.append(card1)
                    self.state.played_cards.append(card2)
                    if card1 == "Pudding":
                        self.state.puddings += 1
                    if card2 == "Pudding":
                        self.state.puddings += 1
                    return

>>>>>>> theirs
        card_index = self.choose_card(hand)
        played_card = hand[card_index]
        response = self.play_card(card_index)

        if response.startswith("OK"):
            self.state.played_cards.append(played_card)
            if played_card == "Pudding":
                self.state.puddings += 1

    def run(self, game_id: str, player_name: str):
        """Main game loop."""
        try:
            self.connect()

            if not self.join_game(game_id, player_name):
                return

            # Signal ready
            response = self.signal_ready()

            # Main game loop
            running = True
            while running:
                # Check for incoming messages
                message = self.receive()
                running = self.handle_message(message)

                # If we received our hand, play a card
                if message.startswith("HAND") and self.state and self.state.hand:
                    self.play_turn()

        except KeyboardInterrupt:
            print("\nDisconnecting...")
        except Exception as e:
            print(f"Error: {e}")
        finally:
            self.disconnect()


def main():
    if len(sys.argv) != 5:
        print("Usage: python sushi_go_client.py <host> <port> <game_id> <player_name>")
        print("Example: python sushi_go_client.py localhost 7878 abc123 MyBot")
        sys.exit(1)

    host = sys.argv[1]
    port = int(sys.argv[2])
    game_id = sys.argv[3]
    player_name = sys.argv[4]

    client = SushiGoClient(host, port)
    client.run(game_id, player_name)


if __name__ == "__main__":
    main()
