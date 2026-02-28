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
    puddings: int = 0

    def __post_init__(self):
        if self.played_cards is None:
            self.played_cards = []


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
                self.state.has_unused_wasabi = any(
                    c == "Wasabi" for c in self.state.played_cards
                ) and not any(
                    c in ("Egg Nigiri", "Salmon Nigiri", "Squid Nigiri")
                    for c in self.state.played_cards
                )

    def choose_card(self, hand: list[str]) -> int:
        """
        Choose which card to play.

        Strategy: score each card based on guaranteed and expected value,
        accounting for combos already built, turns remaining, and wasabi state.

        Args:
            hand: List of card names in your current hand

        Returns:
            Index of the card to play (0-based)
        """
        if len(hand) == 1:
            return 0

        played = self.state.played_cards if self.state else []
        turns_left = len(hand) - 1  # turns remaining after this one
        current_round = self.state.round if self.state else 1

        # Tally combos we've built this round
        tempura_count = played.count("Tempura")
        sashimi_count = played.count("Sashimi")
        dumpling_count = played.count("Dumpling")
        maki_total = sum(int(c[-2]) for c in played if c.startswith("Maki Roll"))
        wasabi_count = played.count("Wasabi")
        nigiri_count = sum(1 for c in played if "Nigiri" in c)
        # Correct unused-wasabi check: each nigiri uses up one wasabi
        has_unused_wasabi = wasabi_count > nigiri_count

        def card_value(card: str) -> float:
            # --- Nigiri: guaranteed points, tripled by wasabi ---
            if card == "Squid Nigiri":
                return 9.0 if has_unused_wasabi else 3.0
            if card == "Salmon Nigiri":
                return 6.0 if has_unused_wasabi else 2.0
            if card == "Egg Nigiri":
                return 3.0 if has_unused_wasabi else 1.0

            # --- Wasabi: multiplier for NEXT nigiri (from a future rotated hand) ---
            if card == "Wasabi":
                if has_unused_wasabi:
                    return 0.1  # Already have one unused; stacking is wasteful
                if turns_left >= 1:
                    return 2.5  # Expected bonus from tripling a future nigiri
                return 0.0

            # --- Tempura: 5 pts per pair ---
            if card == "Tempura":
                if tempura_count % 2 == 1:   # Completes a pair right now
                    return 5.0
                return 2.5 if turns_left >= 1 else 0.0  # Need 1 more turn to finish

            # --- Sashimi: 10 pts per set of 3 ---
            if card == "Sashimi":
                mod = sashimi_count % 3
                if mod == 2:                 # Completes a set right now
                    return 10.0
                if mod == 1:                 # Have 1 spare; need 1 more after this
                    return 3.5 if turns_left >= 1 else 0.0
                # mod == 0: starting fresh, need 2 more after this
                return 3.5 if turns_left >= 2 else 0.0

            # --- Dumpling: marginal value 1/2/3/4/5/0 for 1st–6th ---
            if card == "Dumpling":
                marginals = [1, 2, 3, 4, 5, 0]
                return float(marginals[min(dumpling_count, 5)])

            # --- Maki Rolls: competitive (~1 pt per symbol + commitment bonus) ---
            if card.startswith("Maki Roll"):
                symbols = int(card[-2])
                if maki_total == 0 and turns_left == 0:
                    return 0.3  # Too late to start competing
                # Reward investment: already-committed maki is more valuable
                return symbols * 1.0 + (0.5 if maki_total > 0 else 0.0)

            # --- Pudding: end-of-game +6 / -6 swing ---
            if card == "Pudding":
                # More valuable in later rounds (closer to final tally)
                return 1.0 + (current_round - 1) * 0.5

            # --- Chopsticks: lets us play 2 cards in a future turn ---
            if card == "Chopsticks":
                return 1.0 if turns_left >= 2 else 0.0

            return 0.0

        scores = [(card_value(c), i) for i, c in enumerate(hand)]
        return max(scores)[1]

    def handle_message(self, message: str):
        """Handle a message from the server."""
        if message.startswith("HAND"):
            self.parse_hand(message)
        elif message.startswith("ROUND_START"):
            parts = message.split()
            if self.state:
                self.state.round = int(parts[1])
                self.state.turn = 1
                self.state.played_cards = []
        elif message.startswith("PLAYED"):
            # Cards were revealed, next turn
            if self.state:
                self.state.turn += 1
        elif message.startswith("ROUND_END"):
            # Round ended
            if self.state:
                self.state.played_cards = []
        elif message.startswith("GAME_END"):
            print("Game over!")
            return False
        elif message.startswith("WAITING"):
            # Our move was accepted, waiting for others
            pass
        return True

    def play_turn(self):
        """Play a single turn."""
        if not self.state or not self.state.hand:
            return

        card_index = self.choose_card(self.state.hand)

        # Track the card we're about to play
        played_card = self.state.hand[card_index]

        response = self.play_card(card_index)

        if response.startswith("OK"):
            if self.state:
                self.state.played_cards.append(played_card)

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
