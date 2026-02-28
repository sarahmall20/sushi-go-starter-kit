#!/usr/bin/env python3
"""
Sushi Go Client - Strategic Bot

Plays Sushi Go with the following strategy:
  - Uses wasabi only when a high nigiri is in hand (or will likely come soon);
    if both wasabi and a high nigiri are in hand simultaneously, plays them in
    the SAME turn using chopsticks to guarantee the triple multiplier.
  - Tracks each opponent's pudding and maki totals across rounds and adjusts
    pudding / maki valuation dynamically to win +6 and avoid -6 penalties.
  - Uses chopsticks opportunistically: wasabi+nigiri combo, completing sashimi
    sets, or whenever two cards each score >= 2 pts.
  - Prioritises high-certainty point engines (sashimi sets, nigiri on wasabi)
    over speculative plays.

Usage:
    python sushi_go_client2.py <server_host> <server_port> <game_id> <player_name>

Example:
    python sushi_go_client2.py localhost 7878 abc123 MyBot
"""

import re
import socket
import sys
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GameState:
    """Tracks the current state of the game."""
    game_id: str
    player_id: int
    my_name: str
    hand: list = field(default_factory=list)
    round: int = 1
    turn: int = 1
    played_cards: list = field(default_factory=list)           # our played-area this round
    puddings: int = 0                                          # our cumulative puddings
    player_count: int = 0
    # Per-opponent tracking (keyed by player name)
    opponent_maki: dict = field(default_factory=dict)          # name -> maki symbols this round
    opponent_puddings: dict = field(default_factory=dict)      # name -> cumulative puddings

    # ── Derived convenience helpers ───────────────────────────────────────────
    @property
    def has_chopsticks(self) -> bool:
        return "Chopsticks" in self.played_cards

    @property
    def unused_wasabi_count(self) -> int:
        wasabi = self.played_cards.count("Wasabi")
        nigiri = sum(1 for c in self.played_cards if "Nigiri" in c)
        return max(0, wasabi - nigiri)

    @property
    def has_unused_wasabi(self) -> bool:
        return self.unused_wasabi_count > 0

    def my_maki(self) -> int:
        return sum(int(c[-2]) for c in self.played_cards if c.startswith("Maki Roll"))

    def max_opponent_maki(self) -> int:
        return max(self.opponent_maki.values(), default=0)

    def max_opponent_puddings(self) -> int:
        return max(self.opponent_puddings.values(), default=0)

    def min_opponent_puddings(self) -> int:
        return min(self.opponent_puddings.values(), default=0)


class SushiGoClient:
    """A strategic client for playing Sushi Go."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self.state: Optional[GameState] = None
        self._recv_buffer = ""

    # ── Networking ────────────────────────────────────────────────────────────

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        self._recv_buffer = ""
        print(f"Connected to {self.host}:{self.port}")

    def disconnect(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def send(self, command: str):
        message = command + "\n"
        self.sock.sendall(message.encode("utf-8"))
        print(f">>> {command}")

    def receive(self) -> str:
        while True:
            if "\n" in self._recv_buffer:
                line, self._recv_buffer = self._recv_buffer.split("\n", 1)
                message = line.strip()
                if message:
                    print(f"<<< {message}")
                return message
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Server closed connection")
            self._recv_buffer += chunk.decode("utf-8", errors="replace")

    def receive_until(self, predicate) -> str:
        while True:
            message = self.receive()
            if not message:
                continue
            if predicate(message):
                return message

    # ── Join / Signal ─────────────────────────────────────────────────────────

    def join_game(self, game_id: str, player_name: str) -> bool:
        self.send(f"JOIN {game_id} {player_name}")
        response = self.receive_until(
            lambda l: l.startswith("WELCOME") or l.startswith("ERROR")
        )
        if response.startswith("WELCOME"):
            parts = response.split()
            self.state = GameState(
                game_id=parts[1],
                player_id=int(parts[2]),
                my_name=player_name,
            )
            return True
        print(f"Failed to join: {response}")
        return False

    def signal_ready(self):
        self.send("READY")
        return self.receive()

    # ── Low-level play commands ───────────────────────────────────────────────

    def play_card(self, card_index: int) -> str:
        self.send(f"PLAY {card_index}")
        return self.receive()

    def play_chopsticks(self, index1: int, index2: int) -> str:
        self.send(f"CHOPSTICKS {index1} {index2}")
        return self.receive()

    # ── Parsing ───────────────────────────────────────────────────────────────

    def parse_hand(self, message: str):
        """Parse HAND message and refresh hand state."""
        if not message.startswith("HAND"):
            return
        payload = message[len("HAND "):]
        cards = []
        for match in re.finditer(r"(\d+):(.*?)(?=\s\d+:|$)", payload):
            cards.append(match.group(2).strip())
        if self.state:
            self.state.hand = cards

    def _parse_played(self, message: str):
        """
        Parse PLAYED message to update opponent maki / pudding tracking.

        Format: PLAYED Alice:Squid Nigiri; Bob:Tempura, Wasabi; ...
        A player can have multiple cards listed (comma-separated) when
        chopsticks were used.
        """
        if not self.state:
            return
        payload = message[len("PLAYED "):]
        for segment in payload.split(";"):
            segment = segment.strip()
            if ":" not in segment:
                continue
            colon_idx = segment.index(":")
            player_name = segment[:colon_idx].strip()
            if player_name == self.state.my_name:
                continue  # we track ourselves separately
            cards_str = segment[colon_idx + 1:].strip()
            cards = [c.strip() for c in cards_str.split(",")]
            for card in cards:
                if card == "Pudding":
                    self.state.opponent_puddings[player_name] = (
                        self.state.opponent_puddings.get(player_name, 0) + 1
                    )
                if card.startswith("Maki Roll"):
                    try:
                        symbols = int(card[-2])
                    except ValueError:
                        symbols = 1
                    self.state.opponent_maki[player_name] = (
                        self.state.opponent_maki.get(player_name, 0) + symbols
                    )

    # ── Card Valuation ────────────────────────────────────────────────────────

    def _card_value(self, card: str, turns_left: int) -> float:
        """
        Return an estimated point value for playing *card* right now.

        turns_left: turns remaining AFTER this one (= hand_size - 1).
        """
        s = self.state
        if s is None:
            return 0.0

        played = s.played_cards
        current_round = s.round

        tempura_count = played.count("Tempura")
        sashimi_count = played.count("Sashimi")
        dumpling_count = played.count("Dumpling")
        maki_mine = s.my_maki()
        has_unused_wasabi = s.has_unused_wasabi

        # ── Nigiri ────────────────────────────────────────────────────────────
        # Tripled when we already have an unused wasabi on the table.
        if card == "Squid Nigiri":
            return 9.0 if has_unused_wasabi else 3.0
        if card == "Salmon Nigiri":
            return 6.0 if has_unused_wasabi else 2.0
        if card == "Egg Nigiri":
            return 3.0 if has_unused_wasabi else 1.0

        # ── Wasabi ────────────────────────────────────────────────────────────
        # Value depends on whether we expect a high nigiri soon.
        # If we already have an unused wasabi, don't stack another.
        if card == "Wasabi":
            if has_unused_wasabi:
                return 0.1   # Stacking wasabi is wasteful
            if turns_left >= 1:
                # Expected gain: tripling lifts Squid by +6, Salmon by +4, Egg by +2
                # Rough expected bonus ~4 pts
                return 4.0
            return 0.0   # Last turn; wasabi would never be used

        # ── Tempura: 5 pts per pair ───────────────────────────────────────────
        if card == "Tempura":
            if tempura_count % 2 == 1:
                return 5.0           # Completes a pair RIGHT NOW
            return 2.5 if turns_left >= 1 else 0.0

        # ── Sashimi: 10 pts per set of 3 ─────────────────────────────────────
        if card == "Sashimi":
            mod = sashimi_count % 3
            if mod == 2:
                return 10.0          # Completes a set RIGHT NOW
            if mod == 1:
                return 5.0 if turns_left >= 1 else 0.0
            # mod == 0 -> starting a fresh set
            return 3.0 if turns_left >= 2 else 0.0

        # ── Dumpling: cumulative scoring 1/3/6/10/15 pts ─────────────────────
        if card == "Dumpling":
            marginals = [1, 2, 3, 4, 5, 0]
            return float(marginals[min(dumpling_count, 5)])

        # ── Maki Rolls: competitive ───────────────────────────────────────────
        # Worth pursuing if we're within striking distance of first (6 pts)
        # or second place (3 pts in >=3 players).  Bail if hopelessly behind.
        if card.startswith("Maki Roll"):
            try:
                symbols = int(card[-2])
            except ValueError:
                symbols = 1
            max_opp = s.max_opponent_maki()
            gap = max_opp - maki_mine
            if turns_left == 0 and maki_mine + symbols <= max_opp:
                return 0.2   # Can't win maki; near-zero value
            if gap <= symbols * (turns_left + 1):
                # Realistic chance of winning first place
                return symbols * 1.2 + 0.5
            # Possibly securing second place
            return symbols * 0.7

        # ── Pudding ───────────────────────────────────────────────────────────
        # +6 for most at game end, -6 for fewest (skipped in 2-player).
        # We dynamically adjust value based on our standing vs. opponents.
        if card == "Pudding":
            max_opp_p = s.max_opponent_puddings()
            min_opp_p = s.min_opponent_puddings()
            my_p = s.puddings
            rounds_left_after = 3 - current_round   # rounds still to come

            if s.player_count == 2:
                # 2-player: only +6 for most puddings, no -6 penalty
                base = 1.2
                if my_p <= max_opp_p:
                    base += 0.8   # Need to catch up
                return base + rounds_left_after * 0.3

            # Multiplayer
            if my_p <= min_opp_p:
                # We currently have fewest: danger of -6 penalty
                return 4.5 + rounds_left_after * 0.6
            if my_p < max_opp_p:
                # Mid-pack: moderate incentive
                return 2.5 + rounds_left_after * 0.4
            # We lead puddings: still take them to hold the lead
            return 1.5 + rounds_left_after * 0.3

        # ── Chopsticks ────────────────────────────────────────────────────────
        # Worth having early; minimal value late in round.
        if card == "Chopsticks":
            if turns_left >= 4:
                return 3.0
            if turns_left >= 3:
                return 2.5
            if turns_left == 2:
                return 1.5
            if turns_left == 1:
                return 0.5
            return 0.0

        return 0.0

    # ── Chopstick Decision ────────────────────────────────────────────────────

    def _best_chopstick_play(self, hand: list) -> tuple:
        """
        Decide whether to use chopsticks and which pair to play.

        Returns (should_use: bool, idx1: int, idx2: int).

        Priorities (highest first):
          1. Wasabi + high nigiri in the SAME turn (guarantees the triple).
          2. Complete the final sashimi of a set + another high-value card.
          3. Any two distinct cards both valued >= threshold.
        """
        turns_left = len(hand) - 1

        def val(i: int) -> float:
            return self._card_value(hand[i], turns_left)

        n = len(hand)
        if n < 2:
            return False, 0, 1

        # ── Priority 1: Wasabi + high nigiri together ──────────────────────
        # Playing both at once guarantees the nigiri lands on the wasabi.
        # Only do this when we do NOT already have an unused wasabi played.
        if not self.state.has_unused_wasabi:
            wasabi_indices = [i for i, c in enumerate(hand) if c == "Wasabi"]
            if wasabi_indices:
                wi = wasabi_indices[0]
                for nigiri_name in ("Squid Nigiri", "Salmon Nigiri"):
                    nigiri_indices = [j for j, c in enumerate(hand)
                                      if c == nigiri_name and j != wi]
                    if nigiri_indices:
                        return True, wi, nigiri_indices[0]

        # ── Priority 2: Complete a sashimi set + grab another good card ────
        played = self.state.played_cards if self.state else []
        sashimi_count = played.count("Sashimi")
        if sashimi_count % 3 == 2:
            sashimi_indices = [i for i, c in enumerate(hand) if c == "Sashimi"]
            if sashimi_indices:
                si = sashimi_indices[0]
                best_other = max(
                    ((val(j), j) for j in range(n) if j != si),
                    default=(0.0, 0),
                )
                if best_other[0] >= 2.0:
                    return True, si, best_other[1]

        # ── Priority 3: Two cards both worth a meaningful amount ────────────
        scored = sorted(((val(i), i) for i in range(n)), reverse=True)
        v1, i1 = scored[0]
        v2, i2 = scored[1]

        # Threshold rises late in the round because returning chopsticks to
        # opponents is more costly when there are fewer turns left.
        if turns_left >= 3:
            threshold = 2.5
        elif turns_left == 2:
            threshold = 3.0
        else:
            threshold = 4.0   # Very late: only worthwhile for premium combos

        if v2 >= threshold:
            return True, i1, i2

        return False, 0, 1

    # ── Turn Logic ────────────────────────────────────────────────────────────

    def choose_card(self, hand: list) -> int:
        """Return the index of the single best card to play."""
        if not hand:
            return 0
        turns_left = len(hand) - 1
        scores = [(self._card_value(c, turns_left), i) for i, c in enumerate(hand)]
        return max(scores)[1]

    def play_turn(self):
        """Play one turn, using chopsticks when strategically beneficial."""
        if not self.state or not self.state.hand:
            return

        hand = self.state.hand

        # Attempt chopstick play first
        if self.state.has_chopsticks and len(hand) >= 2:
            should_use, idx1, idx2 = self._best_chopstick_play(hand)
            if should_use:
                response = self.play_chopsticks(idx1, idx2)
                if response.startswith("OK"):
                    card1, card2 = hand[idx1], hand[idx2]
                    # Chopsticks card returns to passing hand; remove from played
                    if "Chopsticks" in self.state.played_cards:
                        self.state.played_cards.remove("Chopsticks")
                    self.state.played_cards.append(card1)
                    self.state.played_cards.append(card2)
                    if card1 == "Pudding":
                        self.state.puddings += 1
                    if card2 == "Pudding":
                        self.state.puddings += 1
                    return

        # Normal single-card play
        card_index = self.choose_card(hand)
        played_card = hand[card_index]
        response = self.play_card(card_index)
        if response.startswith("OK"):
            self.state.played_cards.append(played_card)
            if played_card == "Pudding":
                self.state.puddings += 1

    # ── Message Handling ──────────────────────────────────────────────────────

    def handle_message(self, message: str) -> bool:
        """Process one server message; return False when the game is over."""
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
                self.state.opponent_maki = {}   # reset per-round maki counts

        elif message.startswith("PLAYED"):
            if self.state:
                self.state.turn += 1
                self._parse_played(message)

        elif message.startswith("ROUND_END"):
            if self.state:
                # Puddings from our played area accumulate across rounds
                self.state.puddings += self.state.played_cards.count("Pudding")
                self.state.played_cards = []
                self.state.opponent_maki = {}

        elif message.startswith("GAME_END"):
            print("Game over!")
            return False

        elif message.startswith("WAITING"):
            pass

        return True

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self, game_id: str, player_name: str):
        try:
            self.connect()
            if not self.join_game(game_id, player_name):
                return
            self.signal_ready()

            running = True
            while running:
                message = self.receive()
                if not message:
                    continue
                running = self.handle_message(message)
                if message.startswith("HAND") and self.state and self.state.hand:
                    self.play_turn()

        except KeyboardInterrupt:
            print("\nDisconnecting...")
        except Exception as e:
            print(f"Error: {e}")
            raise
        finally:
            self.disconnect()


def main():
    if len(sys.argv) != 5:
        print("Usage: python sushi_go_client2.py <host> <port> <game_id> <player_name>")
        print("Example: python sushi_go_client2.py localhost 7878 abc123 MyBot")
        sys.exit(1)

    host = sys.argv[1]
    port = int(sys.argv[2])
    game_id = sys.argv[3]
    player_name = sys.argv[4]

    client = SushiGoClient(host, port)
    client.run(game_id, player_name)


if __name__ == "__main__":
    main()
