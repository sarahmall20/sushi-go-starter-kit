#!/usr/bin/env python3
"""
Sushi Go Tournament Client - Strategic Bot

Plays a full tournament using the same strategy as sushi_go_client2.py:
  - PUDDING FIRST: Aggressive pudding collection with context-aware urgency.
  - DENIAL PLAY: Takes cards that block opponents' high-value combos.
  - WASABI+NIGIRI: Pairs them in the same turn via chopsticks for 9 pts.
  - SCORE TRACKING: Parses ROUND_END scores to track who is winning.
  - CHOPSTICK COMBOS: Wasabi+Squid, sashimi completion, tempura completion.

Usage:
    python sushi_go_tournament_client.py <server_host> <server_port> <tournament_id> <player_name>

Example:
    python sushi_go_tournament_client.py localhost 7878 spicy-salmon MyBot
"""

import json
import re
import socket
import sys
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GameState:
    """Tracks the current state of a single game within the tournament."""
    game_id: str = ""
    player_id: int = 0
    rejoin_token: str = ""
    my_name: str = ""
    hand: list = field(default_factory=list)
    round: int = 1
    turn: int = 1
    played_cards: list = field(default_factory=list)           # our played-area this round
    puddings: int = 0                                          # our cumulative puddings
    player_count: int = 0
    # Cumulative scores parsed from ROUND_END
    total_scores: dict = field(default_factory=dict)           # name -> total points so far
    my_total_score: int = 0
    # Per-opponent tracking (keyed by player name)
    opponent_maki: dict = field(default_factory=dict)          # name -> maki symbols this round
    opponent_puddings: dict = field(default_factory=dict)      # name -> cumulative puddings
    opponent_sashimi: dict = field(default_factory=dict)       # name -> sashimi count this round
    opponent_tempura: dict = field(default_factory=dict)       # name -> tempura count this round
    opponent_dumpling: dict = field(default_factory=dict)      # name -> dumpling count this round
    opponent_wasabi: dict = field(default_factory=dict)        # name -> unused wasabi count

    # -- Derived convenience helpers --
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

    def best_opponent_score(self) -> int:
        return max(self.total_scores.values(), default=0)

    def any_opponent_has_wasabi(self) -> bool:
        return any(v > 0 for v in self.opponent_wasabi.values())


class SushiGoTournamentClient:
    """A strategic client for playing Sushi Go tournaments."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self.state: Optional[GameState] = None
        self._recv_buffer = ""
        # Tournament state
        self.tournament_id: str = ""
        self.tournament_rejoin_token: str = ""

    # -- Networking --

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

    # -- Tournament Join / Match --

    def join_tournament(self, tournament_id: str, player_name: str) -> bool:
        self.tournament_id = tournament_id
        self.send(f"TOURNEY {tournament_id} {player_name}")
        response = self.receive_until(
            lambda line: line.startswith("TOURNAMENT_WELCOME") or line.startswith("ERROR")
        )
        if response.startswith("TOURNAMENT_WELCOME"):
            parts = response.split()
            self.tournament_rejoin_token = parts[3] if len(parts) > 3 else ""
            print(f"Joined tournament {tournament_id} (rejoin token: {self.tournament_rejoin_token})")
            return True
        print(f"Failed to join tournament: {response}")
        return False

    def join_match(self, match_token: str, player_name: str) -> bool:
        self.send(f"TJOIN {match_token}")
        response = self.receive_until(
            lambda line: line.startswith("WELCOME") or line.startswith("ERROR")
        )
        if response.startswith("WELCOME"):
            parts = response.split()
            rejoin_token = parts[3] if len(parts) > 3 else ""
            self.state = GameState(
                game_id=parts[1],
                player_id=int(parts[2]),
                rejoin_token=rejoin_token,
                my_name=player_name,
            )
            print(f"Joined match (game: {self.state.game_id})")
            return True
        print(f"Failed to join match: {response}")
        return False

    def signal_ready(self):
        self.send("READY")
        return self.receive()

    def leave_game(self):
        self.send("LEAVE")
        self.receive_until(
            lambda line: line.startswith("OK") or line.startswith("ERROR")
        )
        self.state = None

    # -- Low-level play commands --

    def play_card(self, card_index: int) -> str:
        self.send(f"PLAY {card_index}")
        return self.receive()

    def play_chopsticks(self, index1: int, index2: int) -> str:
        self.send(f"CHOPSTICKS {index1} {index2}")
        return self.receive()

    # -- Parsing --

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
        Parse PLAYED message to update opponent tracking.
        Format: PLAYED Alice:Squid Nigiri; Bob:Tempura, Wasabi; ...
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
                continue
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
                if card == "Sashimi":
                    self.state.opponent_sashimi[player_name] = (
                        self.state.opponent_sashimi.get(player_name, 0) + 1
                    )
                if card == "Tempura":
                    self.state.opponent_tempura[player_name] = (
                        self.state.opponent_tempura.get(player_name, 0) + 1
                    )
                if card == "Dumpling":
                    self.state.opponent_dumpling[player_name] = (
                        self.state.opponent_dumpling.get(player_name, 0) + 1
                    )
                if card == "Wasabi":
                    self.state.opponent_wasabi[player_name] = (
                        self.state.opponent_wasabi.get(player_name, 0) + 1
                    )
                if "Nigiri" in card:
                    cur = self.state.opponent_wasabi.get(player_name, 0)
                    if cur > 0:
                        self.state.opponent_wasabi[player_name] = cur - 1

    # -- Denial Value --

    def _denial_value(self, card: str) -> float:
        """
        Estimate the points DENIED to the next player if we take this card.
        Added on top of our own gain so cards that block opponents' combos get
        extra weight.
        """
        s = self.state
        if s is None or s.player_count < 2:
            return 0.0

        denial = 0.0

        # Deny a Nigiri to someone with unused wasabi
        if card == "Squid Nigiri" and s.any_opponent_has_wasabi():
            denial += 6.0
        if card == "Salmon Nigiri" and s.any_opponent_has_wasabi():
            denial += 4.0
        if card == "Egg Nigiri" and s.any_opponent_has_wasabi():
            denial += 2.0

        # Deny the third sashimi (10 pts)
        if card == "Sashimi":
            max_opp = max(s.opponent_sashimi.values(), default=0)
            if max_opp % 3 == 2:
                denial += 8.0
            elif max_opp % 3 == 1:
                denial += 2.0

        # Deny tempura pair completion
        if card == "Tempura":
            max_opp = max(s.opponent_tempura.values(), default=0)
            if max_opp % 2 == 1:
                denial += 4.0

        # Deny maki leader adding more symbols
        if card.startswith("Maki Roll"):
            try:
                symbols = int(card[-2])
            except ValueError:
                symbols = 1
            my_maki = s.my_maki()
            max_opp = s.max_opponent_maki()
            if max_opp > my_maki + symbols * 2:
                denial += symbols * 0.8

        # Deny pudding to the pudding leader
        if card == "Pudding":
            max_opp_p = s.max_opponent_puddings()
            my_p = s.puddings
            if max_opp_p > my_p:
                denial += 1.5

        # Deny wasabi setup to any opponent
        if card == "Wasabi" and not s.has_unused_wasabi:
            denial += 1.0

        return denial

    # -- Card Valuation --

    def _card_value(self, card: str, turns_left: int) -> float:
        """Return an estimated point value for playing *card* right now."""
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

        # -- Nigiri --
        if card == "Squid Nigiri":
            base = 9.0 if has_unused_wasabi else 4.0
            return base + self._denial_value(card)
        if card == "Salmon Nigiri":
            base = 6.0 if has_unused_wasabi else 3.6
            return base + self._denial_value(card)
        if card == "Egg Nigiri":
            base = 3.0 if has_unused_wasabi else 1.0
            return base + self._denial_value(card)

        # -- Wasabi --
        if card == "Wasabi":
            if has_unused_wasabi:
                return 0.1
            if turns_left >= 5:
                return 3.5
            if turns_left >= 3:
                return 3.0
            if turns_left == 2:
                return 2.0
            if turns_left == 1:
                return 1.0
            return 0.0

        # -- Tempura: 5 pts per pair --
        if card == "Tempura":
            if tempura_count % 2 == 1:
                return 5.0 + self._denial_value(card)
            return (2.5 if turns_left >= 1 else 0.0) + self._denial_value(card)

        # -- Sashimi: 10 pts per set of 3 --
        if card == "Sashimi":
            mod = sashimi_count % 3
            denial = self._denial_value(card)
            if mod == 2:
                return 10.0 + denial
            if mod == 1:
                return (5.0 if turns_left >= 1 else 0.0) + denial
            return (3.0 if turns_left >= 2 else 0.0) + denial

        # -- Dumpling: cumulative scoring 1/3/6/10/15 pts --
        if card == "Dumpling":
            marginals = [1, 2, 3, 4, 5, 0]
            base = float(marginals[min(dumpling_count, 5)])
            if dumpling_count >= 2 and turns_left >= 2:
                base += 0.5
            return base

        # -- Maki Rolls: competitive --
        if card.startswith("Maki Roll"):
            try:
                symbols = int(card[-2])
            except ValueError:
                symbols = 1
            max_opp = s.max_opponent_maki()
            gap = max_opp - maki_mine
            denial = self._denial_value(card)
            if turns_left == 0 and maki_mine + symbols <= max_opp:
                return 0.1 + denial
            if gap <= symbols * (turns_left + 1):
                return symbols * 1.3 + 0.5 + denial
            if s.player_count == 2:
                return 0.2 + denial
            return symbols * 0.5 + denial

        # -- Pudding --
        if card == "Pudding":
            max_opp_p = s.max_opponent_puddings()
            min_opp_p = s.min_opponent_puddings()
            my_p = s.puddings
            rounds_left_after = 3 - current_round
            denial = self._denial_value(card)
            has_opp_data = bool(s.opponent_puddings)

            if s.player_count == 2:
                if not has_opp_data:
                    return 2.5 + rounds_left_after * 0.4 + denial
                if my_p < max_opp_p:
                    return 3.5 + rounds_left_after * 0.5 + denial
                if my_p == max_opp_p:
                    return 3.0 + rounds_left_after * 0.4 + denial
                return 2.0 + rounds_left_after * 0.3 + denial

            if not has_opp_data:
                return 2.0 + rounds_left_after * 0.4 + denial
            if my_p < min_opp_p:
                return 4.5 + rounds_left_after * 0.5 + denial
            if my_p == min_opp_p:
                return 3.0 + rounds_left_after * 0.4 + denial
            if my_p < max_opp_p:
                return 2.0 + rounds_left_after * 0.3 + denial
            if my_p == max_opp_p:
                return 1.8 + rounds_left_after * 0.25 + denial
            return 1.5 + rounds_left_after * 0.2 + denial

        # -- Chopsticks --
        if card == "Chopsticks":
            if s.has_chopsticks:
                return 0.1
            if turns_left >= 4:
                return 2.5
            if turns_left >= 3:
                return 2.0
            if turns_left == 2:
                return 1.0
            if turns_left == 1:
                return 0.3
            return 0.0

        return 0.0

    # -- Chopstick Decision --

    def _best_chopstick_play(self, hand: list) -> tuple:
        """
        Decide whether to use chopsticks and which pair to play.
        Returns (should_use: bool, idx1: int, idx2: int).
        """
        turns_left = len(hand) - 1

        def val(i: int) -> float:
            return self._card_value(hand[i], turns_left)

        n = len(hand)
        if n < 2:
            return False, 0, 1

        # Priority 1: Wasabi + high nigiri together (guarantees triple)
        if not self.state.has_unused_wasabi:
            wasabi_indices = [i for i, c in enumerate(hand) if c == "Wasabi"]
            if wasabi_indices:
                wi = wasabi_indices[0]
                for nigiri_name in ("Squid Nigiri", "Salmon Nigiri"):
                    nigiri_indices = [j for j, c in enumerate(hand)
                                      if c == nigiri_name and j != wi]
                    if nigiri_indices:
                        return True, wi, nigiri_indices[0]

        # Priority 1b: Unused wasabi already played + nigiri + good companion
        if self.state.has_unused_wasabi:
            nigiri_indices = {c: i for i, c in enumerate(hand)
                              if c in ("Squid Nigiri", "Salmon Nigiri", "Egg Nigiri")}
            if nigiri_indices:
                for best_nig in ("Squid Nigiri", "Salmon Nigiri", "Egg Nigiri"):
                    if best_nig in nigiri_indices:
                        ni = nigiri_indices[best_nig]
                        best_other = max(
                            ((val(j), j) for j in range(n) if j != ni),
                            default=(0.0, 0),
                        )
                        if best_other[0] >= 2.5:
                            return True, ni, best_other[1]
                        break

        # Priority 2: Complete a sashimi set + grab another good card
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
                if best_other[0] >= 1.5:
                    return True, si, best_other[1]

        # Priority 2b: Complete a tempura pair + grab another good card
        tempura_count = played.count("Tempura")
        if tempura_count % 2 == 1:
            tempura_indices = [i for i, c in enumerate(hand) if c == "Tempura"]
            if tempura_indices:
                ti = tempura_indices[0]
                best_other = max(
                    ((val(j), j) for j in range(n) if j != ti),
                    default=(0.0, 0),
                )
                if best_other[0] >= 2.0:
                    return True, ti, best_other[1]

        # Priority 3: Two cards both worth a meaningful amount
        scored = sorted(((val(i), i) for i in range(n)), reverse=True)
        v1, i1 = scored[0]
        v2, i2 = scored[1]

        if turns_left >= 3:
            threshold = 2.5
        elif turns_left == 2:
            threshold = 3.0
        else:
            threshold = 4.0

        if v2 >= threshold:
            return True, i1, i2

        return False, 0, 1

    # -- Turn Logic --

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

    # -- Message Handling --

    def handle_game_message(self, message: str) -> bool:
        """Handle an in-game message. Returns False on GAME_END."""
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
                self.state.opponent_sashimi = {}
                self.state.opponent_tempura = {}
                self.state.opponent_dumpling = {}
                self.state.opponent_wasabi = {}

        elif message.startswith("PLAYED"):
            if self.state:
                self.state.turn += 1
                self._parse_played(message)

        elif message.startswith("ROUND_END"):
            if self.state:
                parts = message.split(None, 2)
                if len(parts) >= 3:
                    try:
                        scores = json.loads(parts[2])
                        # ROUND_END scores are cumulative totals -- SET, do not ADD.
                        for name, score in scores.items():
                            if name == self.state.my_name:
                                self.state.my_total_score = score
                            else:
                                self.state.total_scores[name] = score
                    except (json.JSONDecodeError, ValueError, KeyError):
                        pass
                self.state.played_cards = []
                self.state.opponent_maki = {}
                self.state.opponent_sashimi = {}
                self.state.opponent_tempura = {}
                self.state.opponent_dumpling = {}
                self.state.opponent_wasabi = {}

        elif message.startswith("GAME_END"):
            print("Game over!")
            return False

        elif message.startswith("WAITING"):
            pass

        return True

    # -- Game Loop --

    def play_game(self) -> Optional[str]:
        """Play a full game. Returns a pending tournament message if one arrived mid-game."""
        while True:
            message = self.receive()

            # Tournament messages can arrive during a game
            if message.startswith("TOURNAMENT_MATCH") or message.startswith("TOURNAMENT_COMPLETE"):
                return message

            game_running = self.handle_game_message(message)

            if message.startswith("HAND") and self.state and self.state.hand:
                self.play_turn()

            if not game_running:
                return None

    # -- Tournament Loop --

    def run(self, tournament_id: str, player_name: str):
        """Main tournament loop."""
        try:
            self.connect()

            if not self.join_tournament(tournament_id, player_name):
                return

            pending_message = None

            while True:
                if pending_message:
                    msg = pending_message
                    pending_message = None
                else:
                    msg = self.receive()

                if not msg:
                    continue

                if msg.startswith("TOURNAMENT_MATCH"):
                    # TOURNAMENT_MATCH <tid> <match_token> <round> [<opponent>]
                    parts = msg.split()
                    match_token = parts[2]
                    round_num = parts[3]
                    opponent = parts[4] if len(parts) > 4 else "unknown"

                    if match_token == "BYE" or opponent == "BYE":
                        print(f"Round {round_num}: got a BYE, auto-advancing...")
                        continue

                    print(f"Round {round_num}: matched vs {opponent}")

                    if not self.join_match(match_token, player_name):
                        continue

                    self.signal_ready()

                    pending_message = self.play_game()

                    self.leave_game()

                elif msg.startswith("TOURNAMENT_COMPLETE"):
                    parts = msg.split()
                    winner = parts[2] if len(parts) > 2 else "unknown"
                    print(f"Tournament complete! Winner: {winner}")
                    break

                elif msg.startswith("TOURNAMENT_JOINED"):
                    print(f"  {msg}")

        except KeyboardInterrupt:
            print("\nDisconnecting...")
        except Exception as e:
            print(f"Error: {e}")
            raise
        finally:
            self.disconnect()


def main():
    if len(sys.argv) != 5:
        print("Usage: python sushi_go_tournament_client.py <host> <port> <tournament_id> <player_name>")
        print("Example: python sushi_go_tournament_client.py localhost 7878 spicy-salmon MyBot")
        sys.exit(1)

    host = sys.argv[1]
    port = int(sys.argv[2])
    tournament_id = sys.argv[3]
    player_name = sys.argv[4]

    client = SushiGoTournamentClient(host, port)
    client.run(tournament_id, player_name)


if __name__ == "__main__":
    main()
